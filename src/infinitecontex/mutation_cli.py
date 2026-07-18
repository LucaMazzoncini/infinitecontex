"""Offline Typer workflow for explicitly approved G3 repository mutations."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import orjson
import typer
from rich.console import Console

from infinitecontex.events.logger import EventLogger
from infinitecontex.planning.store import PlanStore
from infinitecontex.storage.layout import build_layout
from infinitecontex.task_context.store import TaskContextAnalysisStore
from infinitecontex.tools.builtins import builtin_registry
from infinitecontex.tools.mutation_models import MutationDecision, MutationRequest
from infinitecontex.tools.mutation_service import RepositoryMutationService
from infinitecontex.tools.mutation_store import MutationStore

repo_patch_app = typer.Typer(help="Validate structured UTF-8 mutation requests without applying them")
plan_mutation_app = typer.Typer(help="Propose, decide, and apply task-bound repository mutations")
tool_mutation_app = typer.Typer(help="Inspect compact G3 mutation execution records")
console = Console()


def _service(project_root: Path) -> RepositoryMutationService:
    layout = build_layout(project_root)
    return RepositoryMutationService(
        builtin_registry(),
        PlanStore(layout.plans),
        TaskContextAnalysisStore(layout.plans),
        MutationStore(layout.plans, layout.tool_mutations),
        event_logger=EventLogger(layout.events / "tool-mutations.jsonl"),
    )


def _request(path: Path) -> MutationRequest:
    if path.stat().st_size > 8 * 1024 * 1024:
        raise typer.BadParameter("Mutation request exceeds the 8 MiB input limit")
    return MutationRequest.model_validate(orjson.loads(path.read_bytes()))


def _emit(value: object, as_json: bool) -> None:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    elif isinstance(value, tuple):
        payload = [item.model_dump(mode="json") for item in value]
    else:
        payload = value
    if as_json:
        console.file.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode() + "\n")
    else:
        console.print(payload)


@repo_patch_app.command("validate")
def validate_patch(
    file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)],
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate structure and repository paths; never persist or modify a target."""
    _emit(_service(repo.resolve()).validate_request(repo.resolve(), _request(file)), json)


@plan_mutation_app.command("propose")
def propose_patch(
    plan_id: str,
    task: Annotated[str, typer.Option("--task")],
    file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)],
    revision: Annotated[int | None, typer.Option("--revision", min=1)] = None,
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = (project_root or repo).resolve()
    proposal = _service(root).propose(repo.resolve(), plan_id, task, _request(file), revision=revision)
    if json:
        _emit(proposal, True)
    else:
        console.print(f"Proposal: {proposal.proposal_id}\nFingerprint: {proposal.semantic_fingerprint}")
        console.print(proposal.diff_preview, markup=False)


@plan_mutation_app.command("list")
def list_proposals(
    plan_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.list_proposals(plan_id), json)


@plan_mutation_app.command("show")
def show_proposal(
    plan_id: str,
    proposal_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.load_proposal(plan_id, proposal_id), json)


def _decision(
    plan_id: str,
    proposal_id: str,
    actor: str,
    decision: MutationDecision,
    reason: str | None,
    acknowledge_warnings: bool,
    project_root: Path,
    as_json: bool,
) -> None:
    value = _service(project_root.resolve()).decide(
        plan_id,
        proposal_id,
        actor,
        decision,
        reason=reason,
        warnings_acknowledged=acknowledge_warnings,
    )
    _emit(value, as_json)


@plan_mutation_app.command("approve")
def approve_patch(
    plan_id: str,
    proposal_id: str,
    actor: Annotated[str, typer.Option("--actor")],
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    acknowledge_warnings: Annotated[bool, typer.Option("--acknowledge-warnings")] = False,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decision(plan_id, proposal_id, actor, MutationDecision.APPROVED, reason, acknowledge_warnings, project_root, json)


@plan_mutation_app.command("reject")
def reject_patch(
    plan_id: str,
    proposal_id: str,
    actor: Annotated[str, typer.Option("--actor")],
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decision(plan_id, proposal_id, actor, MutationDecision.REJECTED, reason, False, project_root, json)


@plan_mutation_app.command("apply")
def apply_patch(
    plan_id: str,
    proposal_id: str,
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = (project_root or repo).resolve()
    _emit(_service(root).apply(repo.resolve(), plan_id, proposal_id), json)


@tool_mutation_app.command("list")
def list_mutations(
    limit: Annotated[int, typer.Option("--limit", min=1, max=1000)] = 100,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.list_records(limit=limit), json)


@tool_mutation_app.command("show")
def show_mutation(
    mutation_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.load_record(mutation_id), json)
