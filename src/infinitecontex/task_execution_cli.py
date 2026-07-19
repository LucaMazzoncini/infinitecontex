"""Offline explicit CLI for G7 execution authorization and dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import orjson
import typer
from rich.console import Console

from infinitecontex.mutation_cli import _service as mutation_service
from infinitecontex.planning.store import PlanStore
from infinitecontex.storage.layout import build_layout
from infinitecontex.task_context.store import TaskContextAnalysisStore
from infinitecontex.task_execution.models import ActionRequest, AuthorizationDecision, AuthorizationSpecification
from infinitecontex.task_execution.service import TaskExecutionService
from infinitecontex.task_execution.store import TaskExecutionStore
from infinitecontex.tools.builtins import builtin_read_handler_registry, builtin_registry
from infinitecontex.tools.execution_gateway import ReadOnlyExecutionGateway
from infinitecontex.tools.execution_service import RepositoryReadExecutionService
from infinitecontex.tools.execution_store import ToolExecutionStore
from infinitecontex.tools.sensitive import SensitivePathPolicy
from infinitecontex.validation_cli import _service as validation_service

execution_app = typer.Typer(help="Authorize and dispatch exact task-bound actions")
execution_authorization_app = typer.Typer(help="List and show execution authorization proposals")
execution_grant_app = typer.Typer(help="List, show, and revoke execution grants")
execution_session_app = typer.Typer(help="Start, inspect, and close execution sessions")
console = Console()


def _service(root: Path) -> TaskExecutionService:
    layout = build_layout(root)
    registry = builtin_registry()
    sensitive = SensitivePathPolicy()
    read = RepositoryReadExecutionService(
        registry,
        ReadOnlyExecutionGateway(
            registry,
            builtin_read_handler_registry(registry),
            ToolExecutionStore(layout.tool_executions),
            sensitive_policy=sensitive,
        ),
        plan_store=PlanStore(layout.plans),
        analysis_store=TaskContextAnalysisStore(layout.plans),
        sensitive_policy=sensitive,
    )
    return TaskExecutionService(
        registry,
        PlanStore(layout.plans),
        TaskContextAnalysisStore(layout.plans),
        TaskExecutionStore(layout.plans),
        read_service=read,
        mutation_service=mutation_service(root),
        validation_service=validation_service(root),
    )


def _load(path: Path, kind: type[AuthorizationSpecification] | type[ActionRequest]) -> object:
    if path.stat().st_size > 1024 * 1024:
        raise typer.BadParameter("G7 data-only request exceeds 1 MiB")
    return kind.model_validate(orjson.loads(path.read_bytes()))


def _emit(value: object, as_json: bool) -> None:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    elif isinstance(value, (tuple, list)):
        payload = [x.model_dump(mode="json") for x in value]
    else:
        payload = value
    if as_json:
        console.file.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode() + "\n")
    else:
        console.print(payload)


@execution_app.command("authorize")
def authorize(
    plan_id: str,
    task: Annotated[str, typer.Option("--task")],
    file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)],
    revision: Annotated[int | None, typer.Option("--revision")] = None,
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = (project_root or repo).resolve()
    spec = _load(file, AuthorizationSpecification)
    assert isinstance(spec, AuthorizationSpecification)
    value = _service(root).propose(repo.resolve(), plan_id, task, spec, revision=revision)
    if json:
        _emit(value, True)
    else:
        console.print(f"Authorization proposal: {value.proposal_id}\nFingerprint: {value.semantic_fingerprint}")
        console.print(f"Tools: {', '.join(x.canonical_name for x in value.tools)}")
        console.print(f"Actions: {value.maximum_total_actions}; risk: {value.risk}")
        console.print("Task status grants execution: NO")
        console.print("Requested capability is a grant: NO")
        console.print("Tool execution available before approval: NO")


def _decision(
    plan_id: str, proposal_id: str, actor: str, reason: str, decision: AuthorizationDecision, root: Path, as_json: bool
) -> None:
    _emit(_service(root.resolve()).decide(plan_id, proposal_id, actor, decision, reason=reason), as_json)


@execution_app.command("approve")
def approve(
    plan_id: str,
    proposal_id: str,
    actor: Annotated[str, typer.Option("--actor")],
    reason: Annotated[str, typer.Option("--reason")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decision(plan_id, proposal_id, actor, reason, AuthorizationDecision.APPROVED, project_root, json)


@execution_app.command("reject")
def reject(
    plan_id: str,
    proposal_id: str,
    actor: Annotated[str, typer.Option("--actor")],
    reason: Annotated[str, typer.Option("--reason")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decision(plan_id, proposal_id, actor, reason, AuthorizationDecision.REJECTED, project_root, json)


@execution_app.command("activate")
def activate(
    plan_id: str,
    proposal_id: str,
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = (project_root or repo).resolve()
    _emit(_service(root).activate(repo.resolve(), plan_id, proposal_id), json)


@execution_app.command("run")
def run(
    plan_id: str,
    session: Annotated[str, typer.Option("--session")],
    action_file: Annotated[Path, typer.Option("--action-file", exists=True, dir_okay=False)],
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = (project_root or repo).resolve()
    request = _load(action_file, ActionRequest)
    assert isinstance(request, ActionRequest)
    _emit(_service(root).run(repo.resolve(), plan_id, session, request), json)


@execution_session_app.command("start")
def session_start(
    plan_id: str,
    grant: Annotated[str, typer.Option("--grant")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).start_session(plan_id, grant), json)


@execution_session_app.command("show")
def session_show(
    plan_id: str,
    session_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    service = _service(project_root.resolve())
    _emit(
        {
            "session": service.store.load_session(plan_id, session_id).model_dump(mode="json"),
            "actions": [x.model_dump(mode="json") for x in service.store.list_actions(plan_id, session_id)],
        },
        json,
    )


@execution_session_app.command("close")
def session_close(
    plan_id: str,
    session_id: str,
    actor: Annotated[str, typer.Option("--actor")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    del actor
    _emit(_service(project_root.resolve()).close_session(plan_id, session_id), json)


@execution_grant_app.command("revoke")
def grant_revoke(
    plan_id: str,
    grant_id: str,
    actor: Annotated[str, typer.Option("--actor")],
    reason: Annotated[str, typer.Option("--reason")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).revoke(plan_id, grant_id, actor, reason), json)


def _register(app: typer.Typer, kind: str) -> None:
    @app.command("list")
    def list_items(
        plan_id: str,
        project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
        json: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        store = _service(project_root.resolve()).store
        values = (
            store.list_proposals(plan_id)
            if kind == "proposal"
            else store.list_grants(plan_id)
            if kind == "grant"
            else store.list_sessions(plan_id)
        )
        _emit(values, json)

    @app.command("show")
    def show_item(
        plan_id: str,
        item_id: str,
        project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
        json: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        store = _service(project_root.resolve()).store
        value = (
            store.load_proposal(plan_id, item_id)
            if kind == "proposal"
            else store.load_grant(plan_id, item_id)
            if kind == "grant"
            else store.load_session(plan_id, item_id)
        )
        _emit(value, json)


_register(execution_authorization_app, "proposal")
_register(execution_grant_app, "grant")
