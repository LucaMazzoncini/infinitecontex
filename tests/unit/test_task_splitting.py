from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_task_context import NOW, _plan_input, _profile, _service, _write_repository

from infinitecontex.planning.models import PlannerProvenance, TaskStatus
from infinitecontex.planning.service import PlanningService
from infinitecontex.task_splitting.errors import (
    SplitApprovalError,
    SplitPersistenceError,
    SplitProposalStaleError,
)
from infinitecontex.task_splitting.models import ApplicationStatus, ApprovalDecision
from infinitecontex.task_splitting.service import TaskSplittingService
from infinitecontex.task_splitting.store import TaskSplitStore


def _splitter(tmp_path: Path) -> tuple[TaskSplittingService, str, str]:
    _write_repository(tmp_path)
    context, plan_id = _service(
        tmp_path,
        _plan_input(files=["src/sample.py", "docs/guide.md"]),
        _profile(),
    )
    planning = PlanningService(context.plan_store, clock=lambda: NOW)
    split = TaskSplittingService(
        planning,
        context,
        TaskSplitStore(tmp_path / ".infctx" / "plans"),
        clock=lambda: NOW,
    )
    task_id = context.plan_store.load_current(plan_id).tasks[0].task_id
    return split, plan_id, task_id


def test_optional_split_proposal_has_fitting_leaves_and_is_deterministic(tmp_path: Path) -> None:
    split, plan_id, task_id = _splitter(tmp_path)
    first = split.propose(
        plan_id,
        task_id,
        tmp_path,
        model_name="demo:latest",
        digest="sha256:test",
    )
    second = split.propose(
        plan_id,
        task_id,
        tmp_path,
        model_name="demo:latest",
        digest="sha256:test",
        persist=False,
    )
    assert first.optional_proposal
    assert first.semantic_fingerprint == second.semantic_fingerprint
    assert first.every_leaf_fits and first.validation_passed
    assert len(first.proposed_children) == 2
    assert first.direct_child_count == 2
    assert first.source_context_fit is not None
    assert all(child.context_fit.passing for child in first.proposed_children)
    assert first.contract_coverage.complete
    assert split.store.load_proposal(plan_id, first.proposal_id) == first


def test_split_application_requires_warning_ack_and_creates_revision(tmp_path: Path) -> None:
    split, plan_id, task_id = _splitter(tmp_path)
    proposal = split.propose(
        plan_id,
        task_id,
        tmp_path,
        model_name="demo:latest",
        digest="sha256:test",
    )
    with pytest.raises(SplitApprovalError, match="acknowledge"):
        split.approve_and_apply(
            plan_id,
            proposal.proposal_id,
            tmp_path,
            actor_identifier="tester",
            decision_reason="Reviewed deterministic split.",
        )
    revision, approval = split.approve_and_apply(
        plan_id,
        proposal.proposal_id,
        tmp_path,
        actor_identifier="tester",
        decision_reason="Reviewed deterministic split.",
        warnings_acknowledged=True,
    )
    assert revision.current_revision == 2
    assert revision.graph_fingerprint == proposal.resulting_graph_fingerprint
    assert approval.decision == ApprovalDecision.APPROVED
    assert approval.application_status == ApplicationStatus.APPLIED
    assert split.store.load_approval(plan_id, approval.approval_id) == approval
    history = split.planning.store.history(plan_id)
    assert [item.current_revision for item in history] == [1, 2]
    assert not history[0].tasks[0].metadata.get("split_barrier", False)
    source = next(item for item in revision.tasks if item.task_id == proposal.source_task_id)
    assert source.metadata["split_barrier"] is True
    assert set(source.dependency_ids) == {child.proposed_task_id for child in proposal.proposed_children}
    children = [item for item in revision.tasks if item.task_id in set(source.dependency_ids)]
    assert all(item.parent_task_id == source.task_id for item in children)
    assert revision.task_count == proposal.resulting_task_count


def test_repository_change_invalidates_split_approval(tmp_path: Path) -> None:
    split, plan_id, task_id = _splitter(tmp_path)
    proposal = split.propose(
        plan_id,
        task_id,
        tmp_path,
        model_name="demo:latest",
        digest="sha256:test",
    )
    (tmp_path / "src" / "sample.py").write_text("changed = True\n", encoding="utf-8")
    with pytest.raises(SplitProposalStaleError, match="Repository changed"):
        split.approve_and_apply(
            plan_id,
            proposal.proposal_id,
            tmp_path,
            actor_identifier="tester",
            decision_reason="Stale proposal should fail.",
            warnings_acknowledged=True,
        )


def test_rejection_is_explicit_immutable_and_cannot_be_applied(tmp_path: Path) -> None:
    split, plan_id, task_id = _splitter(tmp_path)
    proposal = split.propose(
        plan_id,
        task_id,
        tmp_path,
        model_name="demo:latest",
        digest="sha256:test",
    )
    approval = split.reject(
        plan_id,
        proposal.proposal_id,
        actor_identifier="Reviewer",
        decision_reason=None,
    )
    assert approval.decision == ApprovalDecision.REJECTED
    assert approval.application_status == ApplicationStatus.NOT_APPLIED
    assert split.planning.store.load_current(plan_id).current_revision == 1
    with pytest.raises(SplitApprovalError, match="duplicate decisions"):
        split.approve_and_apply(
            plan_id,
            proposal.proposal_id,
            tmp_path,
            actor_identifier="Reviewer",
            warnings_acknowledged=True,
        )
    with pytest.raises(SplitApprovalError, match="duplicate decisions"):
        split.reject(
            plan_id,
            proposal.proposal_id,
            actor_identifier="Reviewer",
        )


def test_blank_actor_fails_before_plan_application(tmp_path: Path) -> None:
    split, plan_id, task_id = _splitter(tmp_path)
    proposal = split.propose(
        plan_id,
        task_id,
        tmp_path,
        model_name="demo:latest",
        digest="sha256:test",
    )
    with pytest.raises(ValidationError, match="actor must not be blank"):
        split.approve_and_apply(
            plan_id,
            proposal.proposal_id,
            tmp_path,
            actor_identifier="   ",
            warnings_acknowledged=True,
        )
    assert split.planning.store.load_current(plan_id).current_revision == 1


def test_plan_change_and_proposal_fingerprint_mismatch_fail_closed(tmp_path: Path) -> None:
    split, plan_id, task_id = _splitter(tmp_path)
    proposal = split.propose(
        plan_id,
        task_id,
        tmp_path,
        model_name="demo:latest",
        digest="sha256:test",
    )
    split.planning.transition_task(
        plan_id,
        task_id,
        TaskStatus.IN_PROGRESS,
        reason="Concurrent plan change",
        author=PlannerProvenance.HUMAN_AUTHORED,
    )
    with pytest.raises(SplitProposalStaleError, match="Plan changed"):
        split.approve_and_apply(
            plan_id,
            proposal.proposal_id,
            tmp_path,
            actor_identifier="Reviewer",
            warnings_acknowledged=True,
        )

    second, second_plan_id, second_task_id = _splitter(tmp_path / "second")
    second_proposal = second.propose(
        second_plan_id,
        second_task_id,
        tmp_path / "second",
        model_name="demo:latest",
        digest="sha256:test",
    )
    path = (
        tmp_path
        / "second"
        / ".infctx"
        / "plans"
        / second_plan_id
        / "splits"
        / "proposals"
        / f"{second_proposal.proposal_id}.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["resulting_task_count"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SplitPersistenceError, match="integrity"):
        second.store.load_proposal(second_plan_id, second_proposal.proposal_id)
