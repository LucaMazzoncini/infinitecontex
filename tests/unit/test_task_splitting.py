from __future__ import annotations

from pathlib import Path

import pytest
from test_task_context import NOW, _plan_input, _profile, _service, _write_repository

from infinitecontex.planning.service import PlanningService
from infinitecontex.task_splitting.errors import SplitApprovalError, SplitProposalStaleError
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
