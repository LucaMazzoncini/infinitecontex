"""Offline explicit CLI for G6 state-transition proposals and application."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import orjson
import typer
from rich.console import Console

from infinitecontex.events.logger import EventLogger
from infinitecontex.evidence_review.models import ActorType
from infinitecontex.evidence_review_cli import _service as review_service
from infinitecontex.planning.models import CriterionStatus, TaskStatus
from infinitecontex.planning.service import PlanningService
from infinitecontex.planning.store import PlanStore
from infinitecontex.state_transitions.models import (
    CriterionTransitionRequest,
    StateTransitionApplication,
    StateTransitionApproval,
    StateTransitionProposal,
    TransitionDecision,
)
from infinitecontex.state_transitions.service import StateTransitionService
from infinitecontex.state_transitions.store import StateTransitionStore
from infinitecontex.storage.layout import build_layout

plan_transition_app = typer.Typer(help="Propose, decide, and apply explicit plan-state transitions")
transition_proposal_app = typer.Typer(help="List and show transition proposals")
transition_approval_app = typer.Typer(help="List and show transition approvals")
transition_application_app = typer.Typer(help="List and show transition applications")
console = Console()


def _service(root: Path) -> StateTransitionService:
    layout = build_layout(root)
    reviews = review_service(root)
    plans = PlanStore(layout.plans)
    return StateTransitionService(
        plans,
        reviews.store,
        StateTransitionStore(layout.plans),
        planning=PlanningService(plans),
        review_service=reviews,
        event_logger=EventLogger(layout.events / "state-transitions.jsonl"),
    )


def _emit(value: object, as_json: bool) -> None:
    payload = (
        value.model_dump(mode="json")
        if hasattr(value, "model_dump")
        else [x.model_dump(mode="json") for x in value]
        if isinstance(value, tuple)
        else value
    )
    if as_json:
        console.file.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode() + "\n")
        return
    if isinstance(value, StateTransitionProposal):
        console.print(f"Transition proposal: {value.proposal_id}")
        console.print(f"Plan {value.plan_id}: revision {value.source_plan_revision} -> {value.resulting_revision}")
        target = value.proposed_task_status or value.source_task_status
        console.print(f"Task {value.task_id}: {value.source_task_status.value} -> {target.value}")
        for item in value.criterion_transitions:
            outcome = item.review_outcome.value if item.review_outcome else "human-policy"
            console.print(
                f"Criterion {item.criterion_id}: {item.current_status.value} -> "
                f"{item.proposed_status.value} ({outcome})"
            )
        console.print(f"Eligibility: {value.eligibility.value}; fingerprint: {value.semantic_fingerprint}")
        console.print("Plan changed during proposal creation: NO")
        console.print("Task execution authorized: NO")
        console.print("Source files changed: NO")
    elif isinstance(value, StateTransitionApproval):
        console.print(f"Transition decision: {value.decision.value} by {value.actor}")
        console.print("Plan changed: NO")
    elif isinstance(value, StateTransitionApplication):
        console.print(f"Applied: revision {value.source_revision} -> {value.resulting_revision}")
        console.print(f"Application: {value.application_id}")
    else:
        console.print(payload)


@plan_transition_app.command("propose")
def propose(
    plan_id: str,
    task: Annotated[str, typer.Option("--task")],
    criterion: Annotated[str | None, typer.Option("--criterion")] = None,
    to: Annotated[CriterionStatus | None, typer.Option("--to")] = None,
    review: Annotated[str | None, typer.Option("--review")] = None,
    review_decision: Annotated[str | None, typer.Option("--review-decision")] = None,
    task_to: Annotated[TaskStatus | None, typer.Option("--task-to")] = None,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    acknowledge_warnings: Annotated[bool, typer.Option("--acknowledge-warnings")] = False,
    revision: Annotated[int | None, typer.Option("--revision")] = None,
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if (criterion is None) != (to is None):
        raise typer.BadParameter("--criterion and --to must be supplied together")
    requests = (
        ()
        if criterion is None
        else (
            CriterionTransitionRequest(
                criterion_id=criterion,
                target_status=to if to is not None else CriterionStatus.PENDING,
                review_id=review,
                decision_id=review_decision,
                reason=reason,
                warnings_acknowledged=acknowledge_warnings,
            ),
        )
    )
    root = (project_root or repo).resolve()
    _emit(_service(root).propose(repo.resolve(), plan_id, task, requests, task_target=task_to, revision=revision), json)


def _decide(
    plan_id: str,
    proposal_id: str,
    actor: str,
    reason: str,
    decision: TransitionDecision,
    actor_type: ActorType,
    ack: bool,
    root: Path,
    as_json: bool,
) -> None:
    _emit(
        _service(root.resolve()).decide(
            plan_id, proposal_id, actor, decision, reason=reason, actor_type=actor_type, warnings_acknowledged=ack
        ),
        as_json,
    )


@plan_transition_app.command("approve")
def approve(
    plan_id: str,
    proposal_id: str,
    actor: Annotated[str, typer.Option("--actor")],
    reason: Annotated[str, typer.Option("--reason")],
    actor_type: Annotated[ActorType, typer.Option("--actor-type")] = ActorType.HUMAN,
    acknowledge_warnings: Annotated[bool, typer.Option("--acknowledge-warnings")] = False,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decide(
        plan_id,
        proposal_id,
        actor,
        reason,
        TransitionDecision.APPROVED,
        actor_type,
        acknowledge_warnings,
        project_root,
        json,
    )


@plan_transition_app.command("reject")
def reject(
    plan_id: str,
    proposal_id: str,
    actor: Annotated[str, typer.Option("--actor")],
    reason: Annotated[str, typer.Option("--reason")],
    actor_type: Annotated[ActorType, typer.Option("--actor-type")] = ActorType.HUMAN,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decide(plan_id, proposal_id, actor, reason, TransitionDecision.REJECTED, actor_type, False, project_root, json)


@plan_transition_app.command("apply")
def apply(
    plan_id: str,
    proposal_id: str,
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = (project_root or repo).resolve()
    _emit(_service(root).apply(repo.resolve(), plan_id, proposal_id), json)


def _list(
    kind: str, plan_id: str, root: Path
) -> tuple[StateTransitionProposal, ...] | tuple[StateTransitionApproval, ...] | tuple[StateTransitionApplication, ...]:
    store = _service(root.resolve()).store
    if kind == "proposal":
        return store.list_proposals(plan_id)
    if kind == "approval":
        return store.list_approvals(plan_id)
    return store.list_applications(plan_id)


def _show(
    kind: str, plan_id: str, item_id: str, root: Path
) -> StateTransitionProposal | StateTransitionApproval | StateTransitionApplication:
    store = _service(root.resolve()).store
    if kind == "proposal":
        return store.load_proposal(plan_id, item_id)
    if kind == "approval":
        return store.load_approval(plan_id, item_id)
    return store.load_application(plan_id, item_id)


def _register(app: typer.Typer, kind: str) -> None:
    @app.command("list")
    def list_items(
        plan_id: str,
        project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
        json: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        _emit(_list(kind, plan_id, project_root), json)

    @app.command("show")
    def show_item(
        plan_id: str,
        item_id: str,
        project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
        json: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        _emit(_show(kind, plan_id, item_id, project_root), json)


_register(transition_proposal_app, "proposal")
_register(transition_approval_app, "approval")
_register(transition_application_app, "application")
