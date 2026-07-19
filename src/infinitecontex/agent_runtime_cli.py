"""Explicit CLI for supervised local-model runs."""

from pathlib import Path
from typing import Annotated

import orjson
import typer
from rich.console import Console

from infinitecontex.agent_runtime.models import RuntimeBudgets
from infinitecontex.agent_runtime.service import OllamaModelAdapter, SupervisedAgentService
from infinitecontex.agent_runtime.store import AgentRunStore
from infinitecontex.core.config import load_app_config
from infinitecontex.llm.ollama import OllamaClient
from infinitecontex.model_profiles.store import ModelProfileStore
from infinitecontex.planning.store import PlanStore
from infinitecontex.storage.layout import build_layout
from infinitecontex.task_execution_cli import _service as execution_service

agent_app = typer.Typer(help="Inspect supervised-agent policy")
plan_agent_app = typer.Typer(help="Create and explicitly run supervised local-model agents")
agent_step_app = typer.Typer(help="Inspect compact supervised-agent steps")
agent_mutation_app = typer.Typer(help="Inspect or export mutation candidates")
console = Console()


def _service(root: Path) -> SupervisedAgentService:
    layout = build_layout(root)
    config = load_app_config(root)
    client = OllamaClient(config.llm.base_url, timeout=config.llm.request_timeout_seconds)
    return SupervisedAgentService(
        PlanStore(layout.plans),
        ModelProfileStore(layout.model_profiles),
        execution_service(root),
        AgentRunStore(layout.plans),
        OllamaModelAdapter(client),
    )


def _emit(value: object, as_json: bool) -> None:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    if as_json:
        console.file.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode() + "\n")
    else:
        console.print(payload)


@agent_app.command("policy")
def policy(json: Annotated[bool, typer.Option("--json")] = False) -> None:
    _emit(
        {
            "schema_version": 1,
            "caller": "supervised_local_agent",
            "future_agent": "disabled",
            "maximum_model_calls": 24,
            "maximum_actions": 20,
            "mutation_apply": False,
        },
        json,
    )


@plan_agent_app.command("create")
def create(
    plan_id: str,
    task: Annotated[str, typer.Option("--task")],
    grant: Annotated[str, typer.Option("--grant")],
    session: Annotated[str, typer.Option("--session")],
    model_profile: Annotated[str, typer.Option("--model-profile")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).create(plan_id, task, grant, session, model_profile, RuntimeBudgets()), json)


@plan_agent_app.command("show")
def show(
    plan_id: str,
    run_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.load_run(plan_id, run_id), json)


@plan_agent_app.command("run")
def run(
    plan_id: str,
    run_id: str,
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = (project_root or repo).resolve()
    _emit(_service(root).run(repo.resolve(), plan_id, run_id), json)


@agent_step_app.command("list")
def steps(
    plan_id: str,
    run_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    values = _service(project_root.resolve()).store.list_steps(plan_id, run_id)
    _emit([x.model_dump(mode="json") for x in values], json)


@agent_mutation_app.command("show")
def mutation_show(
    plan_id: str,
    run_id: str,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(_service(project_root.resolve()).store.load_candidate(plan_id, run_id), json)


@agent_mutation_app.command("export")
def mutation_export(
    plan_id: str,
    run_id: str,
    output: Annotated[Path, typer.Option("--output")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
) -> None:
    value = _service(project_root.resolve()).store.load_candidate(plan_id, run_id)
    output.write_bytes(orjson.dumps(value.model_dump(mode="json"), option=orjson.OPT_INDENT_2))
