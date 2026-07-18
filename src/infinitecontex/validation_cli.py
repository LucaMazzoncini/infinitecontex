"""Explicit offline CLI for G4 validation commands."""

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
from infinitecontex.tools.validation_definitions import ValidationCommandRegistry
from infinitecontex.tools.validation_models import ValidationDecision
from infinitecontex.tools.validation_service import ValidationExecutionService
from infinitecontex.tools.validation_store import ValidationStore

validation_app = typer.Typer(help="Inspect trusted validation-command definitions")
validation_command_app = typer.Typer(help="List and show trusted command definitions")
plan_validation_app = typer.Typer(help="Propose, decide, and run task-bound validations")
tool_validation_app = typer.Typer(help="Inspect compact validation execution records")
validation_evidence_app = typer.Typer(help="Inspect compact validation evidence")
validation_app.add_typer(validation_command_app, name="command")
console = Console()


def _service(root: Path) -> ValidationExecutionService:
    layout = build_layout(root)
    return ValidationExecutionService(
        builtin_registry(),
        ValidationCommandRegistry(),
        PlanStore(layout.plans),
        TaskContextAnalysisStore(layout.plans),
        ValidationStore(layout.plans, layout.tool_validations, layout.validation_evidence),
        event_logger=EventLogger(layout.events / "tool-validations.jsonl"),
    )


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


def _params(values: list[str]) -> dict[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {}
    for item in values:
        if "=" not in item:
            raise typer.BadParameter("Parameters must use name=value")
        name, value = item.split("=", 1)
        if not name or name in result and value in result[name]:
            raise typer.BadParameter("Duplicate or empty validation parameter")
        result.setdefault(name, []).append(value)
    return {key: tuple(value) for key, value in result.items()}


@validation_command_app.command("list")
def command_list(json: Annotated[bool, typer.Option("--json")] = False) -> None:
    _emit(ValidationCommandRegistry().definitions, json)


@validation_command_app.command("show")
def command_show(command_id: str, json: Annotated[bool, typer.Option("--json")] = False) -> None:
    _emit(ValidationCommandRegistry().get(command_id), json)


@plan_validation_app.command("propose")
def propose(
    plan_id: str,
    task: Annotated[str, typer.Option("--task")],
    command: Annotated[str, typer.Option("--command")],
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
    revision: Annotated[int | None, typer.Option("--revision")] = None,
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = (project_root or repo).resolve()
    value = _service(root).propose(repo.resolve(), plan_id, task, command, _params(param or []), revision=revision)
    _emit(value, json)
    if not json:
        console.print(f"Exact arguments: {[value.executable.resolved_path, *value.arguments]}")
        console.print(f"Fingerprint: {value.semantic_fingerprint}")


@plan_validation_app.command("list")
def proposal_list(
    plan_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.list_proposals(plan_id), json)


@plan_validation_app.command("show")
def proposal_show(
    plan_id: str,
    proposal_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.load_proposal(plan_id, proposal_id), json)


def _decide(
    plan_id: str,
    proposal_id: str,
    actor: str,
    decision: ValidationDecision,
    reason: str | None,
    acknowledge_warnings: bool,
    root: Path,
    as_json: bool,
) -> None:
    _emit(
        _service(root.resolve()).decide(
            plan_id, proposal_id, actor, decision, reason=reason, warnings_acknowledged=acknowledge_warnings
        ),
        as_json,
    )


@plan_validation_app.command("approve")
def approve(
    plan_id: str,
    proposal_id: str,
    actor: Annotated[str, typer.Option("--actor")],
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    acknowledge_warnings: Annotated[bool, typer.Option("--acknowledge-warnings")] = False,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decide(plan_id, proposal_id, actor, ValidationDecision.APPROVED, reason, acknowledge_warnings, project_root, json)


@plan_validation_app.command("reject")
def reject(
    plan_id: str,
    proposal_id: str,
    actor: Annotated[str, typer.Option("--actor")],
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decide(plan_id, proposal_id, actor, ValidationDecision.REJECTED, reason, False, project_root, json)


@plan_validation_app.command("run")
def run(
    plan_id: str,
    proposal_id: str,
    criterion: Annotated[str | None, typer.Option("--criterion")] = None,
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(
        _service((project_root or repo).resolve()).run(repo.resolve(), plan_id, proposal_id, criterion_id=criterion),
        json,
    )


@tool_validation_app.command("list")
def record_list(
    limit: Annotated[int, typer.Option("--limit", min=1, max=1000)] = 100,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.list_records(limit), json)


@tool_validation_app.command("show")
def record_show(
    execution_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.load_record(execution_id), json)


@validation_evidence_app.command("list")
def evidence_list(
    limit: Annotated[int, typer.Option("--limit", min=1, max=1000)] = 100,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.list_evidence(limit), json)


@validation_evidence_app.command("show")
def evidence_show(
    evidence_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.load_evidence(evidence_id), json)
