"""Offline Typer workflow for deterministic G5 evidence review."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import orjson
import typer
from rich.console import Console

from infinitecontex.events.logger import EventLogger
from infinitecontex.evidence_review.models import (
    ActorType,
    EvidenceReview,
    EvidenceReviewDecision,
    ReviewDecisionValue,
)
from infinitecontex.evidence_review.service import EvidenceReviewService
from infinitecontex.evidence_review.store import EvidenceReviewStore
from infinitecontex.planning.store import PlanStore
from infinitecontex.storage.layout import build_layout
from infinitecontex.task_context.store import TaskContextAnalysisStore
from infinitecontex.tools.validation_store import ValidationStore

plan_evidence_app = typer.Typer(help="Deterministically review typed validation evidence")
plan_evidence_review_app = typer.Typer(help="List, show, accept, or reject evidence reviews")
plan_evidence_decision_app = typer.Typer(help="List and show immutable evidence-review decisions")
console = Console()


def _service(root: Path) -> EvidenceReviewService:
    layout = build_layout(root)
    return EvidenceReviewService(
        PlanStore(layout.plans),
        ValidationStore(layout.plans, layout.tool_validations, layout.validation_evidence),
        EvidenceReviewStore(layout.plans),
        analysis_store=TaskContextAnalysisStore(layout.plans),
        event_logger=EventLogger(layout.events / "evidence-reviews.jsonl"),
    )


def _payload(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [item.model_dump(mode="json") for item in value]
    return value


def _emit(value: object, as_json: bool) -> None:
    payload = _payload(value)
    if as_json:
        console.file.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode() + "\n")
        return
    if isinstance(value, EvidenceReview):
        console.print(f"Review: {value.review_id}")
        console.print(f"Task / criterion: {value.task_id} / {value.criterion_id}")
        console.print(f"Evidence examined: {value.evidence_count}")
        console.print(
            f"Accepted {len(value.accepted_evidence_ids)}, rejected {len(value.rejected_evidence_ids)}, "
            f"duplicate {len(value.duplicate_evidence_ids)}, conflicting {len(value.conflicting_evidence_ids)}"
        )
        console.print(f"Outcome: {value.outcome.value}; confidence: {value.confidence.value}")
        console.print(
            f"Repository: {value.repository_state_result.value}; output: {value.output_integrity_result.value}"
        )
        if value.reason_codes:
            console.print("Reasons: " + ", ".join(value.reason_codes))
        if value.remediation:
            console.print("Remediation: " + " ".join(value.remediation))
        console.print("Criterion status changed: NO")
        console.print("Task status changed: NO")
    elif isinstance(value, EvidenceReviewDecision):
        console.print(f"Reviewer: {value.actor} ({value.actor_type.value})")
        console.print(f"Decision: {value.decision.value}; reason: {value.reason}")
        console.print("This decision does not complete the criterion or task.")
    else:
        console.print(payload)


@plan_evidence_app.command("review")
def review_evidence(
    plan_id: str,
    task: Annotated[str, typer.Option("--task")],
    criterion: Annotated[str, typer.Option("--criterion")],
    evidence: Annotated[list[str] | None, typer.Option("--evidence")] = None,
    revision: Annotated[int | None, typer.Option("--revision", min=1)] = None,
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = (project_root or repo).resolve()
    _emit(
        _service(root).review(repo.resolve(), plan_id, task, criterion, evidence_ids=evidence, revision=revision), json
    )


@plan_evidence_review_app.command("list")
def list_reviews(
    plan_id: str,
    task: Annotated[str | None, typer.Option("--task")] = None,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.list_reviews(plan_id, task_id=task), json)


@plan_evidence_review_app.command("show")
def show_review(
    plan_id: str,
    review_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    value = _service(project_root.resolve()).store.load_review(plan_id, review_id)
    _service(project_root.resolve()).assert_fresh(value)
    _emit(value, json)


def _decide(
    plan_id: str,
    review_id: str,
    actor: str,
    actor_type: ActorType,
    decision: ReviewDecisionValue,
    reason: str,
    acknowledge_warnings: bool,
    project_root: Path,
    as_json: bool,
) -> None:
    _emit(
        _service(project_root.resolve()).decide(
            plan_id,
            review_id,
            actor,
            decision,
            reason=reason,
            actor_type=actor_type,
            warnings_acknowledged=acknowledge_warnings,
        ),
        as_json,
    )


@plan_evidence_review_app.command("accept")
def accept_review(
    plan_id: str,
    review_id: str,
    actor: Annotated[str, typer.Option("--actor")],
    reason: Annotated[str, typer.Option("--reason")],
    actor_type: Annotated[ActorType, typer.Option("--actor-type")] = ActorType.HUMAN,
    acknowledge_warnings: Annotated[bool, typer.Option("--acknowledge-warnings")] = False,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decide(
        plan_id,
        review_id,
        actor,
        actor_type,
        ReviewDecisionValue.ACCEPTED,
        reason,
        acknowledge_warnings,
        project_root,
        json,
    )


@plan_evidence_review_app.command("reject")
def reject_review(
    plan_id: str,
    review_id: str,
    actor: Annotated[str, typer.Option("--actor")],
    reason: Annotated[str, typer.Option("--reason")],
    actor_type: Annotated[ActorType, typer.Option("--actor-type")] = ActorType.HUMAN,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decide(plan_id, review_id, actor, actor_type, ReviewDecisionValue.REJECTED, reason, False, project_root, json)


@plan_evidence_decision_app.command("list")
def list_decisions(
    plan_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.list_decisions(plan_id), json)


@plan_evidence_decision_app.command("show")
def show_decision(
    plan_id: str,
    decision_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.load_decision(plan_id, decision_id), json)
