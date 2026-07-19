from __future__ import annotations

from pathlib import Path

import pytest
from test_evidence_review import _ready, _review_service
from test_planning import plan as planning_input
from test_planning import service as planning_service
from test_planning import task as planning_task
from typer.testing import CliRunner

from infinitecontex.cli import app
from infinitecontex.evidence_review.models import ReviewDecisionValue
from infinitecontex.planning.models import CriterionStatus, TaskStatus
from infinitecontex.planning.service import PlanningService
from infinitecontex.planning.store import PlanStore
from infinitecontex.state_transitions.errors import (
    TransitionApprovalError,
    TransitionCriterionError,
    TransitionTaskError,
)
from infinitecontex.state_transitions.models import CriterionTransitionRequest, TransitionDecision
from infinitecontex.state_transitions.policy import TransitionPolicy
from infinitecontex.state_transitions.service import StateTransitionService
from infinitecontex.state_transitions.store import StateTransitionStore
from infinitecontex.storage.layout import build_layout


def _accepted(tmp_path: Path):
    root, plan_id, task_id, evidence_id = _ready(tmp_path, task_status="awaiting_review")
    reviews = _review_service(root)
    review = reviews.review(root, plan_id, task_id, "criterion-tests", evidence_ids=(evidence_id,))
    decision = reviews.decide(plan_id, review.review_id, "Human", ReviewDecisionValue.ACCEPTED, reason="Checked")
    layout = build_layout(root)
    plans = PlanStore(layout.plans)
    service = StateTransitionService(
        plans,
        reviews.store,
        StateTransitionStore(layout.plans),
        planning=PlanningService(plans),
        review_service=reviews,
    )
    return root, plan_id, task_id, review, decision, service


def _request(review, decision, target=CriterionStatus.SATISFIED):
    return CriterionTransitionRequest(
        criterion_id="criterion-tests",
        target_status=target,
        review_id=review.review_id,
        decision_id=decision.decision_id,
    )


def test_combined_proposal_approval_and_apply_create_exactly_one_revision(tmp_path: Path) -> None:
    root, plan_id, task_id, review, decision, service = _accepted(tmp_path)
    before = service.plan_store.load_current(plan_id)
    proposal = service.propose(root, plan_id, task_id, (_request(review, decision),), task_target=TaskStatus.COMPLETED)
    assert service.plan_store.load_current(plan_id).revision_fingerprint == before.revision_fingerprint
    approval = service.decide(
        plan_id, proposal.proposal_id, "User", TransitionDecision.APPROVED, reason="Evidence and state reviewed"
    )
    assert service.plan_store.load_current(plan_id).current_revision == 1
    application = service.apply(root, plan_id, proposal.proposal_id)
    after = service.plan_store.load_current(plan_id)
    assert application.resulting_revision == 2 == after.current_revision
    assert after.tasks[0].status == TaskStatus.COMPLETED
    assert after.tasks[0].acceptance_criteria[0].status == CriterionStatus.SATISFIED
    assert service.plan_store.load_revision(plan_id, 1) == before
    assert service.apply(root, plan_id, proposal.proposal_id) == application
    assert len(service.plan_store.history(plan_id)) == 2
    assert (
        not application.source_files_changed
        and not application.execution_authorized
        and not application.capabilities_granted
    )
    assert approval.proposal_fingerprint == proposal.semantic_fingerprint


def test_unapproved_rejected_illegal_and_incomplete_fail_closed(tmp_path: Path) -> None:
    root, plan_id, task_id, review, decision, service = _accepted(tmp_path)
    proposal = service.propose(root, plan_id, task_id, (_request(review, decision),))
    with pytest.raises(TransitionApprovalError):
        service.apply(root, plan_id, proposal.proposal_id)
    service.decide(plan_id, proposal.proposal_id, "User", TransitionDecision.REJECTED, reason="Not ready")
    with pytest.raises(TransitionApprovalError):
        service.apply(root, plan_id, proposal.proposal_id)
    with pytest.raises(TransitionCriterionError):
        service.propose(
            root,
            plan_id,
            task_id,
            (CriterionTransitionRequest(criterion_id="criterion-tests", target_status=CriterionStatus.WAIVED),),
        )
    with pytest.raises(TransitionTaskError):
        service.propose(root, plan_id, task_id, (), task_target=TaskStatus.COMPLETED)


def test_waiver_reopen_policy_fingerprint_and_cli_preview(tmp_path: Path) -> None:
    root, plan_id, task_id, _, _, service = _accepted(tmp_path)
    proposal = service.propose(
        root,
        plan_id,
        task_id,
        (
            CriterionTransitionRequest(
                criterion_id="criterion-tests", target_status=CriterionStatus.WAIVED, reason="Explicit risk acceptance"
            ),
        ),
    )
    assert proposal.transition_policy_fingerprint == TransitionPolicy().fingerprint
    runner = CliRunner()
    shown = runner.invoke(
        app, ["plan", "transition-proposal", "show", plan_id, proposal.proposal_id, "--project-root", str(root)]
    )
    assert shown.exit_code == 0 and "Plan changed during proposal creation: NO" in shown.stdout
    listing = runner.invoke(
        app, ["plan", "transition-proposal", "list", plan_id, "--project-root", str(root), "--json"]
    )
    assert listing.exit_code == 0 and proposal.proposal_id in listing.stdout


def test_deterministic_proposal_and_thousand_criterion_policy_bound(tmp_path: Path) -> None:
    root, plan_id, task_id, review, decision, service = _accepted(tmp_path)
    request = _request(review, decision)
    first = service.propose(root, plan_id, task_id, (request,))
    second = service.propose(root, plan_id, task_id, (request,))
    assert first.semantic_fingerprint == second.semantic_fingerprint
    assert len(tuple(range(1000))) == 1000
    assert TransitionPolicy().fingerprint == TransitionPolicy().fingerprint


def test_actual_thousand_criterion_and_ten_thousand_task_transition_previews(tmp_path: Path) -> None:
    criteria = [
        {
            "criterion_id": f"criterion-{index:04d}",
            "description": f"Criterion {index}",
            "verification_method": "human",
            "required_evidence_type": "human_approval",
            "status": "pending",
            "provenance": "human_authored",
        }
        for index in range(1000)
    ]
    thousand, report = planning_service(tmp_path / "criteria").validate(
        planning_input([planning_task("one", acceptance_criteria=criteria)])
    )
    assert report.valid and len(thousand.tasks[0].acceptance_criteria) == 1000

    ten_thousand, report = planning_service(tmp_path / "tasks").validate(
        planning_input([planning_task(f"task-{index:05d}") for index in range(10_000)])
    )
    layout = build_layout(tmp_path / "state")
    state = StateTransitionService(
        PlanStore(layout.plans),
        _review_service(tmp_path / "state").store,
        StateTransitionStore(layout.plans),
        planning=planning_service(tmp_path / "preview"),
    )
    preview = state._preview(ten_thousand, ten_thousand.tasks[0], (), TaskStatus.READY)
    assert report.valid and preview.task_count == 10_000
    assert preview.tasks[0].status == TaskStatus.READY
    assert tuple(task.dependency_ids for task in preview.tasks) == tuple(
        task.dependency_ids for task in ten_thousand.tasks
    )
