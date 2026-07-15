"""CLI for Infinite Context."""

from __future__ import annotations

import fnmatch
import time
from pathlib import Path
from typing import Annotated, Callable, cast

import orjson
import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from watchfiles import Change, watch

from infinitecontex.context_admission.gate import ContextAdmissionGate
from infinitecontex.context_budget.calculator import ContextBudgetCalculator
from infinitecontex.context_budget.estimation import ConservativeTextEstimator, Utf8ByteUpperBoundEstimator
from infinitecontex.context_budget.models import ContextSectionCategory, ContextSectionInput
from infinitecontex.context_budget.service import ContextBudgetService
from infinitecontex.context_packing.models import ContextCandidate, ContextManifest
from infinitecontex.context_packing.service import ContextPackingService
from infinitecontex.context_packing.store import ContextManifestStore
from infinitecontex.core.config import AppConfig, load_app_config
from infinitecontex.core.models import PromptMode
from infinitecontex.llm.ollama import OllamaClient
from infinitecontex.model_profiles.models import ModelProfile
from infinitecontex.model_profiles.service import ModelProfileService
from infinitecontex.model_profiles.store import ModelProfileStore
from infinitecontex.planning.models import PlanInput, PlanRevision, PlanValidationReport
from infinitecontex.planning.service import PlanningService
from infinitecontex.service import InfiniteContextService
from infinitecontex.storage.layout import build_layout
from infinitecontex.task_context.service import TaskContextService
from infinitecontex.version import __version__

app = typer.Typer(help="Infinite Context: local-first project memory engine", invoke_without_command=True)
model_app = typer.Typer(help="Inspect local model configuration")
profile_app = typer.Typer(help="Create and inspect digest-bound model profiles")
budget_app = typer.Typer(help="Inspect deterministic context budgets")
context_app = typer.Typer(help="Rank and pack explicit context candidates")
manifest_app = typer.Typer(help="Inspect persisted context manifests")
admission_app = typer.Typer(help="Inspect compact context admission records")
plan_app = typer.Typer(help="Validate and inspect strict persisted task DAGs")
context_analysis_app = typer.Typer(help="Inspect persisted task-context analyses")
ESTIMATE_TEXT_FILE_LIMIT_BYTES = 8 * 1024 * 1024
CONTEXT_CANDIDATE_FILE_LIMIT_BYTES = 8 * 1024 * 1024
model_app.add_typer(profile_app, name="profile")
model_app.add_typer(budget_app, name="budget")
app.add_typer(model_app, name="model")
context_app.add_typer(manifest_app, name="manifest")
context_app.add_typer(admission_app, name="admission")
app.add_typer(context_app, name="context")
app.add_typer(plan_app, name="plan")
plan_app.add_typer(context_analysis_app, name="context-analysis")
console = Console()
_global_project_root: Path | None = None


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Show version and exit")] = False,
    project_root: Annotated[Path | None, typer.Option("--project-root", help="Project root for commands")] = None,
) -> None:
    global _global_project_root
    _global_project_root = project_root
    if version:
        console.print(f"infinitecontex {__version__}")
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit(0)


def _effective_project_root(project_root: Path | None) -> Path:
    return (project_root or _global_project_root or Path.cwd()).resolve()


def _service(project_root: Path | None) -> InfiniteContextService:
    return InfiniteContextService(_effective_project_root(project_root))


def _model_profile_service(project_root: Path | None) -> ModelProfileService:
    root = _effective_project_root(project_root)
    cfg = load_app_config(root)
    return ModelProfileService(
        OllamaClient(cfg.llm.base_url, timeout=cfg.llm.request_timeout_seconds),
        ModelProfileStore(build_layout(root).model_profiles),
    )


def _context_budget_service(project_root: Path | None) -> ContextBudgetService:
    root = _effective_project_root(project_root)
    cfg = load_app_config(root)
    return ContextBudgetService(
        ModelProfileStore(build_layout(root).model_profiles),
        ConservativeTextEstimator(),
        ContextBudgetCalculator(cfg.context_budget.warning_threshold_basis_points),
    )


def _context_packing_service(project_root: Path | None) -> ContextPackingService:
    root = _effective_project_root(project_root)
    cfg = load_app_config(root)
    layout = build_layout(root)
    return ContextPackingService(
        ModelProfileStore(layout.model_profiles),
        ContextBudgetCalculator(cfg.context_budget.warning_threshold_basis_points),
        manifest_store=ContextManifestStore(layout.context_manifests),
    )


def _context_admission_gate(project_root: Path | None) -> ContextAdmissionGate:
    from infinitecontex.context_admission.store import AdmissionRecordStore

    layout = build_layout(_effective_project_root(project_root))
    return ContextAdmissionGate(
        ModelProfileStore(layout.model_profiles),
        ContextManifestStore(layout.context_manifests),
        AdmissionRecordStore(layout.context_admissions),
    )


def _planning_service(project_root: Path | None) -> PlanningService:
    from infinitecontex.planning.store import PlanStore

    layout = build_layout(_effective_project_root(project_root))
    return PlanningService(PlanStore(layout.plans))


def _task_context_service(project_root: Path | None) -> TaskContextService:
    from infinitecontex.planning.store import PlanStore
    from infinitecontex.task_context.repository import RepositoryInventoryService
    from infinitecontex.task_context.store import TaskContextAnalysisStore

    root = _effective_project_root(project_root)
    layout = build_layout(root)
    cfg = load_app_config(root)
    return TaskContextService(
        PlanStore(layout.plans),
        ModelProfileStore(layout.model_profiles),
        ContextBudgetCalculator(cfg.context_budget.warning_threshold_basis_points),
        RepositoryInventoryService(),
        TaskContextAnalysisStore(layout.plans),
        ContextManifestStore(layout.context_manifests),
    )


@plan_app.command("validate")
def plan_validate(
    file: Annotated[Path, typer.Option("--file", help="Strict UTF-8 plan JSON")],
    allow_large_file: Annotated[bool, typer.Option("--allow-large-file")] = False,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate and normalize a plan without persisting it."""
    service = _planning_service(None)
    plan = cast(
        PlanInput,
        _run_action(lambda: service.parse_file(file, allow_large_file=allow_large_file), emit=False),
    )
    _, report = service.validate(plan)
    _emit(report.model_dump(mode="json"), json, "plan_validation")
    if not report.valid:
        raise typer.Exit(2)


@plan_app.command("import")
def plan_import(
    file: Annotated[Path, typer.Option("--file", help="Strict UTF-8 plan JSON")],
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    reason: Annotated[str, typer.Option("--reason")] = "Explicit CLI plan import",
    allow_large_file: Annotated[bool, typer.Option("--allow-large-file")] = False,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Persist a completely valid plan as an immutable revision."""
    service = _planning_service(project_root)
    plan = cast(
        PlanInput,
        _run_action(lambda: service.parse_file(file, allow_large_file=allow_large_file), emit=False),
    )
    revision, report, persisted = cast(
        tuple[PlanRevision, PlanValidationReport, bool],
        _run_action(lambda: service.import_plan(plan, revision_reason=reason), emit=False),
    )
    _emit(
        {
            "persisted": persisted,
            "plan": revision.model_dump(mode="json"),
            "validation": report.model_dump(mode="json"),
        },
        json,
        "plan_import",
    )


@plan_app.command("list")
def plan_list(
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    plans = cast(list[PlanRevision], _run_action(_planning_service(project_root).store.list_current, emit=False))
    payload = [
        {
            "plan_id": item.plan_id,
            "title": item.title,
            "status": item.status.value,
            "revision": item.current_revision,
            "task_count": item.task_count,
            "graph_fingerprint": item.graph_fingerprint,
            "updated_at": item.updated_at.isoformat(),
        }
        for item in plans
    ]
    _emit(payload, json, "plans")


@plan_app.command("show")
def plan_show(
    plan_id: Annotated[str, typer.Argument()],
    revision: Annotated[int | None, typer.Option("--revision", min=1)] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    plan, report = cast(
        tuple[PlanRevision, PlanValidationReport],
        _run_action(lambda: _planning_service(project_root).inspect(plan_id, revision), emit=False),
    )
    _emit(
        {"plan": plan.model_dump(mode="json"), "validation": report.model_dump(mode="json")},
        json,
        "plan",
    )


@plan_app.command("history")
def plan_history(
    plan_id: Annotated[str, typer.Argument()],
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    revisions = cast(
        list[PlanRevision],
        _run_action(lambda: _planning_service(project_root).store.history(plan_id), emit=False),
    )
    _emit(
        [
            {
                "revision": item.current_revision,
                "revision_fingerprint": item.revision_fingerprint,
                "previous_revision_fingerprint": item.previous_revision_fingerprint,
                "reason": item.revision_reason,
                "changed_task_ids": item.changed_task_ids,
                "created_at": item.updated_at.isoformat(),
            }
            for item in revisions
        ],
        json,
        "plan_history",
    )


@plan_app.command("graph")
def plan_graph(
    plan_id: Annotated[str, typer.Argument()],
    revision: Annotated[int | None, typer.Option("--revision", min=1)] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    plan, report = cast(
        tuple[PlanRevision, PlanValidationReport],
        _run_action(lambda: _planning_service(project_root).inspect(plan_id, revision), emit=False),
    )
    tasks = {task.task_id: task for task in plan.tasks}
    payload = [
        {
            "order": index + 1,
            "task_id": task_id,
            "task_key": tasks[task_id].task_key,
            "status": tasks[task_id].status.value,
            "dependencies": tasks[task_id].dependency_ids,
            "depth": report.readiness.dependency_depths[task_id] if report.readiness else None,
        }
        for index, task_id in enumerate(report.topological_task_ids)
    ]
    _emit(payload, json, "plan_graph")


@plan_app.command("ready")
def plan_ready(
    plan_id: Annotated[str, typer.Argument()],
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    plan, report = cast(
        tuple[PlanRevision, PlanValidationReport],
        _run_action(lambda: _planning_service(project_root).inspect(plan_id), emit=False),
    )
    ready_ids = set(report.readiness.ready_task_ids if report.readiness else ())
    payload = [
        {"task_id": task.task_id, "task_key": task.task_key, "title": task.title, "status": task.status.value}
        for task in plan.tasks
        if task.task_id in ready_ids
    ]
    _emit(payload, json, "plan_ready")


@plan_app.command("resolve")
def plan_resolve(
    plan_id: Annotated[str, typer.Argument()],
    task: Annotated[str | None, typer.Option("--task")] = None,
    revision: Annotated[int | None, typer.Option("--revision", min=1)] = None,
    repo: Annotated[Path | None, typer.Option("--repo", help="Local repository to inspect")] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Resolve explicit task path and symbol declarations without packing."""
    repository_root = (repo or Path.cwd()).resolve()
    plan, inventory, reports = cast(
        tuple[PlanRevision, object, tuple[object, ...]],
        _run_action(
            lambda: _task_context_service(project_root).resolve_plan(
                plan_id,
                repository_root,
                revision=revision,
                task_id=task,
            ),
            emit=False,
        ),
    )
    payload = {
        "plan_id": plan.plan_id,
        "revision": plan.current_revision,
        "repository_snapshot": getattr(inventory, "snapshot").model_dump(mode="json"),
        "reports": [getattr(item, "model_dump")(mode="json") for item in reports],
    }
    _emit(payload, json, "task_resolution")


@plan_app.command("context-fit")
def plan_context_fit(
    plan_id: Annotated[str, typer.Argument()],
    model: Annotated[str | None, typer.Option("--model")] = None,
    digest: Annotated[str | None, typer.Option("--digest")] = None,
    task: Annotated[str | None, typer.Option("--task")] = None,
    revision: Annotated[int | None, typer.Option("--revision", min=1)] = None,
    repo: Annotated[Path | None, typer.Option("--repo", help="Local repository to inspect")] = None,
    persist: Annotated[bool, typer.Option("--persist/--no-persist")] = True,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Resolve, freeze, pack, and inspect declared task context offline."""
    repository_root = (repo or Path.cwd()).resolve()
    from infinitecontex.task_context.models import TaskContextAnalysis

    analyses = cast(
        tuple[TaskContextAnalysis, ...],
        _run_action(
            lambda: _task_context_service(project_root).context_fit(
                plan_id,
                repository_root,
                model_name=model,
                digest=digest,
                revision=revision,
                task_id=task,
                persist=persist,
            ),
            emit=False,
        ),
    )
    payload = [item.model_dump(mode="json") for item in analyses]
    _emit(payload, json, "task_context_analyses")
    if any(not item.passing for item in analyses):
        raise typer.Exit(2)


@context_analysis_app.command("list")
def plan_context_analysis_list(
    plan_id: Annotated[str, typer.Argument()],
    revision: Annotated[int | None, typer.Option("--revision", min=1)] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    service = _task_context_service(project_root)
    selected_revision = revision or service.plan_store.load_current(plan_id).current_revision
    analyses = cast(
        list[object],
        _run_action(lambda: service.analysis_store.list(plan_id, selected_revision), emit=False),
    )
    payload = [getattr(item, "model_dump")(mode="json") for item in analyses]
    _emit(payload, json, "task_context_analyses")


@context_analysis_app.command("show")
def plan_context_analysis_show(
    plan_id: Annotated[str, typer.Argument()],
    analysis_id: Annotated[str, typer.Argument()],
    revision: Annotated[int | None, typer.Option("--revision", min=1)] = None,
    repo: Annotated[Path | None, typer.Option("--repo", help="Repository used for staleness check")] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    service = _task_context_service(project_root)
    selected_revision = revision or service.plan_store.load_current(plan_id).current_revision
    from infinitecontex.task_context.models import TaskContextAnalysis

    analysis = cast(
        TaskContextAnalysis,
        _run_action(
            lambda: service.analysis_store.load(plan_id, selected_revision, analysis_id),
            emit=False,
        ),
    )
    repository_root = (repo or Path.cwd()).resolve()
    payload = {
        "analysis": analysis.model_dump(mode="json"),
        "staleness": service.staleness(analysis, repository_root).model_dump(mode="json"),
    }
    _emit(payload, json, "task_context_analysis")


@context_app.command("admit")
def context_admit(
    request_file: Annotated[Path, typer.Option("--request-file", help="UTF-8 admission request JSON")],
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    allow_large_file: Annotated[bool, typer.Option("--allow-large-file")] = False,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Evaluate a frozen request offline; never dispatch to Ollama."""
    from infinitecontex.context_admission.models import AdmissionRequest

    data = request_file.read_bytes()
    if len(data) > CONTEXT_CANDIDATE_FILE_LIMIT_BYTES and not allow_large_file:
        raise ValueError("admission request exceeds the safe 8 MiB limit; pass --allow-large-file explicitly")
    request = AdmissionRequest.model_validate(orjson.loads(data))
    result = _context_admission_gate(project_root).evaluate(request)
    _emit(result.model_dump(mode="json"), json, "context_admission")


@admission_app.command("list")
def context_admission_list(
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    records = _context_admission_gate(project_root).record_store.list_records()
    _emit([item.model_dump(mode="json") for item in records], json, "context_admissions")


@admission_app.command("show")
def context_admission_show(
    admission_id: Annotated[str, typer.Argument()],
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    record = _context_admission_gate(project_root).record_store.load(admission_id)
    _emit(record.model_dump(mode="json"), json, "context_admission_record")


@context_app.command("pack")
def context_pack(
    model: Annotated[str, typer.Option("--model", help="Persisted Ollama model profile name")],
    candidate_file: Annotated[Path, typer.Option("--candidate-file", help="UTF-8 candidate JSON file")],
    digest: Annotated[str | None, typer.Option("--digest")] = None,
    caller_reserved_tokens: Annotated[int, typer.Option("--already-reserved-tokens", min=0)] = 0,
    persist: Annotated[bool, typer.Option("--persist/--no-persist")] = True,
    allow_large_file: Annotated[
        bool,
        typer.Option("--allow-large-file", help="Explicitly allow candidate files larger than 8 MiB"),
    ] = False,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create an inspection-only packing manifest from explicit JSON candidates."""
    candidates = _run_action(
        lambda: _read_context_candidates(candidate_file, allow_large_file),
        emit=False,
    )
    service = _context_packing_service(project_root)
    manifest = _run_action(
        lambda: service.pack(
            model,
            cast(list[ContextCandidate], candidates),
            digest=digest,
            caller_reserved_input_tokens=caller_reserved_tokens,
            persist=persist,
        ),
        emit=False,
    )
    if hasattr(manifest, "model_dump"):
        _emit(manifest.model_dump(mode="json"), json, "context_manifest")


@manifest_app.command("list")
def context_manifest_list(
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List persisted context manifests without contacting Ollama."""
    store = _context_packing_service(project_root).manifest_store
    assert store is not None
    manifests = _run_action(store.list_manifests, emit=False)
    payload = [
        {
            "manifest_id": item.manifest_id,
            "calculated_at": item.calculated_at.isoformat(),
            "model": item.model_identity.model_name,
            "digest": item.model_identity.model_digest,
            "decision": item.decision,
            "included_tokens": item.included_token_total,
            "remaining_tokens": item.remaining_pack_tokens,
        }
        for item in cast(list[ContextManifest], manifests)
    ]
    _emit(payload, json, "context_manifests")


@manifest_app.command("show")
def context_manifest_show(
    manifest_id: Annotated[str, typer.Argument()],
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show one persisted context manifest."""
    store = _context_packing_service(project_root).manifest_store
    assert store is not None
    manifest = _run_action(lambda: store.load(manifest_id), emit=False)
    if hasattr(manifest, "model_dump"):
        _emit(manifest.model_dump(mode="json"), json, "context_manifest")


def _read_context_candidates(path: Path, allow_large_file: bool) -> list[ContextCandidate]:
    data = path.read_bytes()
    if len(data) > CONTEXT_CANDIDATE_FILE_LIMIT_BYTES and not allow_large_file:
        raise ValueError(
            f"candidate file is {len(data)} bytes; the safe default limit is "
            f"{CONTEXT_CANDIDATE_FILE_LIMIT_BYTES} bytes (pass --allow-large-file to process it explicitly)"
        )
    raw = orjson.loads(data)
    values = raw.get("candidates") if isinstance(raw, dict) else raw
    if not isinstance(values, list):
        raise ValueError("candidate JSON must be an array or an object with a `candidates` array")
    return [ContextCandidate.model_validate(value) for value in values]


@budget_app.command("show")
def model_budget_show(
    model: Annotated[str, typer.Argument()],
    digest: Annotated[str | None, typer.Option("--digest")] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show fixed reserves and available input using a persisted exact profile."""
    service = _context_budget_service(project_root)
    result = _run_action(lambda: service.calculate(model, [], digest=digest), emit=False)
    if hasattr(result, "model_dump"):
        _emit(result.model_dump(mode="json"), json, "model_budget")


@budget_app.command("estimate")
def model_budget_estimate(
    model: Annotated[str, typer.Argument()],
    text: Annotated[str | None, typer.Option("--text", help="Text to estimate")] = None,
    text_file: Annotated[Path | None, typer.Option("--text-file", help="UTF-8 text file to estimate")] = None,
    digest: Annotated[str | None, typer.Option("--digest")] = None,
    output_tokens: Annotated[int | None, typer.Option("--output-tokens", min=0)] = None,
    tool_result_tokens: Annotated[int | None, typer.Option("--tool-result-tokens", min=0)] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Estimate supplied text against a persisted model profile without contacting Ollama."""
    if (text is None) == (text_file is None):
        _print_error("Provide exactly one of `--text` or `--text-file`.")
        raise typer.Exit(2)
    try:
        if text is not None:
            content = text
        else:
            assert text_file is not None
            content = text_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _print_error(f"Could not read `{text_file}` as UTF-8: {exc}. Fix the file and retry.")
        raise typer.Exit(2) from exc
    section = ContextSectionInput(
        name="input",
        category=ContextSectionCategory.CURRENT_USER_REQUEST,
        text=content,
        mandatory=True,
    )
    service = _context_budget_service(project_root)
    result = _run_action(
        lambda: service.calculate(
            model,
            [section],
            digest=digest,
            requested_output_tokens=output_tokens,
            requested_tool_result_tokens=tool_result_tokens,
        ),
        emit=False,
    )
    if hasattr(result, "model_dump"):
        _emit(result.model_dump(mode="json"), json, "model_budget")


@budget_app.command("estimate-text")
def model_budget_estimate_text(
    text: Annotated[str | None, typer.Option("--text", help="Text to estimate")] = None,
    text_file: Annotated[Path | None, typer.Option("--text-file", help="UTF-8 text file to estimate")] = None,
    allow_large_file: Annotated[
        bool,
        typer.Option("--allow-large-file", help="Explicitly allow files larger than 8 MiB"),
    ] = False,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect raw deterministic estimation without a profile, Ollama, or network access."""
    if (text is None) == (text_file is None):
        _print_error("Provide exactly one of `--text` or `--text-file`.")
        raise typer.Exit(2)
    try:
        content = text if text is not None else _read_estimation_file(cast(Path, text_file), allow_large_file)
    except (OSError, UnicodeError, ValueError) as exc:
        _print_error(f"Could not read `{text_file}`: {exc}. Fix the input and retry.")
        raise typer.Exit(2) from exc

    estimate = ConservativeTextEstimator().estimate(content)
    legacy = Utf8ByteUpperBoundEstimator().estimate(content)
    payload = {
        "strategy": estimate.strategy_name,
        "strategy_version": estimate.strategy_version,
        "estimated_tokens": estimate.token_count,
        "provenance": estimate.provenance,
        "measured": False,
        "exact": False,
        "normalization": estimate.normalization,
        "normalized_characters": estimate.normalized_characters,
        "normalized_utf8_bytes": estimate.normalized_utf8_bytes,
        "content_class": estimate.content_class,
        "conservatism": estimate.conservatism,
        "legacy_strategy": legacy.strategy_name,
        "legacy_estimated_tokens": legacy.token_count,
    }
    _emit(payload, json, "token_estimate")


def _read_estimation_file(path: Path, allow_large_file: bool) -> str:
    data = path.read_bytes()
    if len(data) > ESTIMATE_TEXT_FILE_LIMIT_BYTES and not allow_large_file:
        raise ValueError(
            f"file is {len(data)} bytes; the safe default limit is {ESTIMATE_TEXT_FILE_LIMIT_BYTES} bytes "
            "(pass --allow-large-file to process it explicitly)"
        )
    return data.decode("utf-8")


@profile_app.command("list")
def model_profile_list(
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List persisted model profiles without contacting Ollama."""
    service = _model_profile_service(project_root)
    profiles = _run_action(service.store.list_profiles, emit=False)
    payload = [profile.model_dump(mode="json") for profile in cast(list[ModelProfile], profiles)]
    _emit(payload, json, "model_profiles")


@profile_app.command("show")
def model_profile_show(
    model: Annotated[str, typer.Argument()],
    digest: Annotated[str | None, typer.Option("--digest")] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show a persisted profile; require a digest when multiple builds exist."""
    from infinitecontex.model_profiles.errors import ModelProfileNotFoundError

    service = _model_profile_service(project_root)

    def resolve_profile() -> object:
        if digest is not None:
            return service.store.find_exact("ollama", model, digest)
        matches = service.store.find_by_name("ollama", model)
        if not matches:
            raise ModelProfileNotFoundError(f"No persisted profile exists for ollama/{model}")
        if len(matches) > 1:
            raise ModelProfileNotFoundError(
                f"Multiple builds exist for {model}; rerun with --digest and the exact model digest"
            )
        return matches[0]

    profile = _run_action(resolve_profile, emit=False)
    if hasattr(profile, "model_dump"):
        _emit(profile.model_dump(mode="json"), json, "model_profile")


@profile_app.command("create")
def model_profile_create(
    model: Annotated[str, typer.Argument()],
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect an installed model and persist an uncalibrated conservative profile."""
    service = _model_profile_service(project_root)
    result = _run_action(lambda: service.create_or_reuse(model), emit=False)
    profile, created = cast(tuple[ModelProfile, bool], result)
    payload = profile.model_dump(mode="json")
    payload["created"] = created
    _emit(payload, json, "model_profile")


@app.command()
def setup(
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    check_only: Annotated[bool, typer.Option("--check-only", help="Inspect without writing")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Accept safe local configuration writes")] = False,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check local readiness and add safe Ollama/chat configuration."""
    from infinitecontex.setup.service import SetupService

    root = _effective_project_root(project_root)
    allow_write = yes
    if not check_only and not yes and not json:
        allow_write = typer.confirm("Initialize or update additive .infctx configuration?", default=True)
    cfg = load_app_config(root)
    client = OllamaClient(cfg.llm.base_url, timeout=cfg.llm.request_timeout_seconds)
    report = _run_action(
        lambda: SetupService(root, client).run(check_only=check_only, allow_config_write=allow_write),
        emit=False,
    )
    if hasattr(report, "model_dump"):
        _emit(report.model_dump(mode="json"), json, "setup")


@app.command()
def chat(
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    model: Annotated[str | None, typer.Option("--model", help="Override configured Ollama model")] = None,
    no_snapshot: Annotated[bool, typer.Option("--no-snapshot")] = False,
) -> None:
    """Start the first read-only streaming Ollama chat."""
    from infinitecontex.chat.application import ChatApplication
    from infinitecontex.chat.terminal import run_terminal
    from infinitecontex.setup.service import recommend_model

    root = _effective_project_root(project_root)
    cfg = load_app_config(root)
    client = OllamaClient(cfg.llm.base_url, timeout=cfg.llm.request_timeout_seconds)
    installed = client.list_models()
    selected = model or cfg.llm.model
    if selected == "auto":
        selected = recommend_model([item.name for item in installed])
    installed_model = next((item for item in installed if item.name.casefold() == selected.casefold()), None)
    if installed_model is None or not installed_model.digest:
        _print_error("The selected installed model needs a verified digest. Run `infctx setup` and retry.")
        raise typer.Exit(3)
    from infinitecontex.context_admission.dispatch import GatedChatDispatcher
    from infinitecontex.context_admission.gate import ContextAdmissionGate
    from infinitecontex.context_admission.store import AdmissionRecordStore

    layout = build_layout(root)
    profile_store = ModelProfileStore(layout.model_profiles)
    profile = profile_store.find_exact("ollama", selected, installed_model.digest)
    manifest_store = ContextManifestStore(layout.context_manifests)
    packing_service = ContextPackingService(
        profile_store,
        ContextBudgetCalculator(cfg.context_budget.warning_threshold_basis_points),
        manifest_store=manifest_store,
    )
    dispatcher = GatedChatDispatcher(
        ContextAdmissionGate(
            profile_store,
            manifest_store,
            AdmissionRecordStore(layout.context_admissions),
        ),
        client,
    )
    application = ChatApplication(
        root,
        dispatcher,
        packing_service,
        profile,
        selected,
        max_turns=cfg.chat.recent_turns,
        auto_snapshot=cfg.chat.auto_snapshot and not no_snapshot,
    )
    try:
        run_terminal(application, console)
    except Exception as exc:
        _print_error(f"Chat failed: {exc}. Check Ollama with `infctx setup --check-only`.")
        raise typer.Exit(3) from exc


def _print_error(message: str) -> None:
    console.print(Panel(message, title="Error", border_style="red", expand=False))


def _run_action(
    callback: Callable[[], object],
    *,
    as_json: bool = False,
    format_type: str = "generic",
    progress_message: str | None = None,
    emit: bool = True,
) -> object:
    try:
        if progress_message:
            with console.status(progress_message):
                payload = callback()
        else:
            payload = callback()
    except Exception as exc:
        _print_error(str(exc))
        raise typer.Exit(1) from exc

    if emit:
        _emit(payload, as_json, format_type)
    return payload


def _matches_pattern(rel_path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/**"):
            prefix = pattern[: -len("/**")].rstrip("/")
            if rel_path == prefix or rel_path.startswith(prefix + "/"):
                return True
        if pattern.startswith("**/") and fnmatch.fnmatch(rel_path, pattern[3:]):
            return True
        if fnmatch.fnmatch(rel_path, pattern):
            return True
    return False


def _filter_watch_changes(changes: set[tuple[Change, str]], root: Path, exclude_patterns: list[str]) -> list[str]:
    relevant: list[str] = []
    for _, changed_path in changes:
        try:
            rel_path = Path(changed_path).resolve().relative_to(root).as_posix()
        except Exception:
            continue
        if _matches_pattern(rel_path, exclude_patterns):
            continue
        if rel_path not in relevant:
            relevant.append(rel_path)
    return sorted(relevant)[:12]


def _format_dict(d: dict[str, object], title: str) -> Panel:
    table = Table(show_header=False, box=None)
    table.add_column("Key", style="bold cyan")
    table.add_column("Value", style="none")
    for k, v in d.items():
        if isinstance(v, list) and not v:
            table.add_row(str(k), "[dim]None[/dim]")
        elif isinstance(v, list):
            table.add_row(str(k), "\n".join(f"- {item}" for item in v))
        else:
            table.add_row(str(k), str(v))
    return Panel(table, title=f"[bold]{title}[/bold]", border_style="blue", expand=False)


def _emit(payload: object, as_json: bool, format_type: str = "generic") -> None:
    if as_json:
        console.print(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode(), markup=False, soft_wrap=True)
        return

    if isinstance(payload, str):
        console.print(payload)
    elif isinstance(payload, dict):
        if format_type == "doctor":
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Check")
            table.add_column("Status")
            for k, v in payload.items():
                color = "green" if v == "ok" else "red"
                val_text = f"[{color}]{v}[/{color}]" if isinstance(v, str) else str(v)
                table.add_row(str(k).capitalize(), val_text)
            console.print(
                Panel(
                    table,
                    title="[bold]Doctor Diagnostics[/bold]",
                    title_align="left",
                    border_style="cyan",
                    expand=False,
                )
            )
        elif format_type == "status":
            from rich.columns import Columns

            latest = payload.get("latest_snapshot") or "None"
            latest_created_at = payload.get("latest_snapshot_created_at") or "None"
            snapshot_count = payload.get("snapshot_count", 0)
            branch = payload.get("branch", "unknown")
            root_dir = payload.get("project_root", "")
            pins = payload.get("pins", [])
            commits = payload.get("recent_commits", [])
            developer_goal = payload.get("developer_goal") or "None"
            active_tasks = payload.get("active_tasks", [])
            unresolved = payload.get("unresolved_issues", [])

            dash_table = Table.grid(padding=1)
            dash_table.add_column(style="bold cyan", justify="right")
            dash_table.add_column(style="white")
            dash_table.add_row("Root:", str(root_dir))
            dash_table.add_row("Branch:", f"[green]{branch}[/green]")
            dash_table.add_row("Memory:", f"[magenta]{latest}[/magenta]")
            dash_table.add_row("Captured:", str(latest_created_at))
            dash_table.add_row("Snapshots:", str(snapshot_count))
            dash_table.add_row("Goal:", str(developer_goal))

            pin_text = "\n".join(f"• {p}" for p in pins) if pins else "[dim]No active pins.[/dim]"
            commit_text = "\n".join(f"• {c}" for c in commits) if commits else "[dim]No recent commits.[/dim]"
            task_text = (
                "\n".join(f"• {item}" for item in active_tasks) if active_tasks else "[dim]No active tasks.[/dim]"
            )
            issue_text = "\n".join(f"• {item}" for item in unresolved) if unresolved else "[dim]No open issues.[/dim]"

            cols = Columns(
                [
                    Panel(dash_table, title="[bold]Overview[/bold]", border_style="blue", padding=(1, 2)),
                    Panel(
                        pin_text,
                        title="[bold yellow]Pinned Context[/bold yellow]",
                        border_style="yellow",
                        padding=(1, 2),
                    ),
                    Panel(
                        task_text,
                        title="[bold green]Active Tasks[/bold green]",
                        border_style="green",
                        padding=(1, 2),
                    ),
                    Panel(
                        commit_text,
                        title="[bold magenta]Recent Commits[/bold magenta]",
                        border_style="magenta",
                        padding=(1, 2),
                    ),
                    Panel(
                        issue_text,
                        title="[bold red]Open Issues[/bold red]",
                        border_style="red",
                        padding=(1, 2),
                    ),
                ],
                expand=True,
                equal=True,
            )

            console.print(Panel(cols, title="[bold]Infinite Context Dashboard[/bold]", border_style="cyan", padding=1))

        elif format_type == "init":
            console.print(_format_dict(payload, "Initialization Status"))
        elif format_type == "snapshot":
            console.print(
                Panel(
                    f"[green]Snapshot created:[/green] {payload.get('id', 'unknown')}\n\n"
                    "[dim]Artifacts updated in .infctx/agents/[/dim]",
                    title="[bold]Snapshot[/bold]",
                    border_style="green",
                    expand=False,
                )
            )
        elif format_type == "config":
            console.print(_format_dict(payload, "Configuration"))
        elif format_type == "model_profile":
            identity = payload.get("model_identity", {})
            summary = {
                "profile_id": payload.get("profile_id", ""),
                "model": identity.get("model_name", "") if isinstance(identity, dict) else "",
                "digest": identity.get("model_digest", "unverified") if isinstance(identity, dict) else "",
                "identity": identity.get("identity_strength", "") if isinstance(identity, dict) else "",
                "configured_context_tokens": payload.get("configured_context_tokens", ""),
                "operational_context_tokens": payload.get("operational_context_tokens", ""),
                "maximum_recommended_input_tokens": payload.get("maximum_recommended_input_tokens", ""),
                "calibration": payload.get("calibration_status", ""),
                "created": payload.get("created", ""),
            }
            console.print(_format_dict(summary, "Model Profile"))
        elif format_type == "model_budget":
            reserves = payload.get("fixed_reserves", {})
            identity = payload.get("model_identity", {})
            summary = {
                "model": identity.get("model_name", "") if isinstance(identity, dict) else "",
                "digest": identity.get("model_digest", "") if isinstance(identity, dict) else "",
                "operational_context_tokens": payload.get("operational_context_tokens", ""),
                "fixed_reserves": reserves.get("total_tokens", "") if isinstance(reserves, dict) else "",
                "maximum_recommended_input_tokens": payload.get("maximum_recommended_input_tokens", ""),
                "proposed_input_tokens": payload.get("total_proposed_input_tokens", ""),
                "remaining_input_tokens": payload.get("remaining_input_tokens", ""),
                "utilization_basis_points": payload.get("utilization_basis_points", ""),
                "decision": payload.get("decision", ""),
                "estimation_confidence": payload.get("estimation_confidence", ""),
                "enforcement": payload.get("enforcement", ""),
                "warnings": payload.get("warnings", []),
            }
            console.print(_format_dict(summary, "Context Budget"))
        elif format_type == "token_estimate":
            console.print(_format_dict(payload, "Conservative Token Estimate"))
        elif format_type == "context_manifest":
            included = payload.get("included", [])
            excluded = payload.get("excluded", [])
            summary = {
                "manifest_id": payload.get("manifest_id", ""),
                "decision": payload.get("decision", ""),
                "model_profile_id": payload.get("model_profile_id", ""),
                "estimator": payload.get("estimator_strategy", ""),
                "available_pack_tokens": payload.get("available_pack_tokens", 0),
                "included_tokens": payload.get("included_token_total", 0),
                "remaining_tokens": payload.get("remaining_pack_tokens", 0),
                "included": [
                    f"{item.get('candidate', {}).get('candidate_id')}: {item.get('reason')}"
                    for item in included
                    if isinstance(item, dict) and isinstance(item.get("candidate"), dict)
                ],
                "excluded": [
                    f"{item.get('candidate_id')}: {item.get('reason')} ({item.get('detail')})"
                    for item in excluded
                    if isinstance(item, dict)
                ],
                "warnings": payload.get("warnings", []),
                "enforcement": payload.get("enforcement", ""),
            }
            console.print(_format_dict(summary, "Context Packing Manifest"))
        elif format_type == "task_resolution":
            reports = payload.get("reports", [])
            snapshot = payload.get("repository_snapshot", {})
            path_lines: list[str] = []
            symbol_lines: list[str] = []
            if isinstance(reports, list):
                for report in reports:
                    if not isinstance(report, dict):
                        continue
                    path_lines.extend(
                        f"{item.get('reference', {}).get('original_value')}: {item.get('outcome')}"
                        for item in report.get("path_resolutions", [])
                        if isinstance(item, dict) and isinstance(item.get("reference"), dict)
                    )
                    symbol_lines.extend(
                        f"{item.get('reference', {}).get('original_reference')}: {item.get('outcome')}"
                        for item in report.get("symbol_resolutions", [])
                        if isinstance(item, dict) and isinstance(item.get("reference"), dict)
                    )
            summary = {
                "plan_id": payload.get("plan_id", ""),
                "revision": payload.get("revision", ""),
                "snapshot": snapshot.get("snapshot_id", "") if isinstance(snapshot, dict) else "",
                "repository_dirty": snapshot.get("dirty", "") if isinstance(snapshot, dict) else "",
                "paths": path_lines,
                "symbols": symbol_lines,
            }
            console.print(_format_dict(summary, "Task Context Resolution"))
        elif format_type == "task_context_analysis":
            analysis = payload.get("analysis", {})
            staleness = payload.get("staleness", {})
            summary = {
                "analysis_id": analysis.get("analysis_id", "") if isinstance(analysis, dict) else "",
                "decision": analysis.get("decision", "") if isinstance(analysis, dict) else "",
                "packed_tokens": analysis.get("packed_token_total", 0) if isinstance(analysis, dict) else 0,
                "remaining_tokens": analysis.get("remaining_input_tokens", 0) if isinstance(analysis, dict) else 0,
                "stale": staleness.get("stale", "") if isinstance(staleness, dict) else "",
                "stale_reasons": staleness.get("reasons", []) if isinstance(staleness, dict) else [],
            }
            console.print(_format_dict(summary, "Task Context Analysis"))
        elif format_type == "ingest_chat":
            summary = {
                "developer_goal": payload.get("developer_goal", ""),
                "active_tasks": payload.get("active_tasks", []),
                "decisions": payload.get("decisions", []),
                "unresolved_issues": payload.get("unresolved_issues", []),
                "selected_source": payload.get("selected_source"),
                "selected_path": payload.get("selected_path"),
            }
            console.print(_format_dict(summary, "Chat Ingestion Result"))
        elif format_type == "session":
            console.print(_format_dict(payload, "Session Capture"))
        elif format_type == "restore":
            console.print(_format_dict(payload, "Restore Report"))
        elif format_type == "snapshot_detail":
            from rich.columns import Columns

            overview = Table.grid(padding=1)
            overview.add_column(style="bold cyan", justify="right")
            overview.add_column(style="white")
            overview.add_row("Snapshot:", str(payload.get("id", "")))
            overview.add_row("Created:", str(payload.get("created_at", "")))
            overview.add_row("Branch:", str(payload.get("working_set", {}).get("branch", "")))
            overview.add_row("Goal:", str(payload.get("intent", {}).get("developer_goal", "") or "None"))
            overview.add_row("Prompt:", str(payload.get("prompt_path", "")))

            metrics = payload.get("metrics", {})
            metrics_text = "\n".join(f"• {key}: {value}" for key, value in metrics.items()) or "[dim]No metrics[/dim]"
            tasks = payload.get("intent", {}).get("active_tasks", [])
            issues = payload.get("intent", {}).get("unresolved_issues", [])
            active_files = payload.get("working_set", {}).get("active_files", [])

            cols = Columns(
                [
                    Panel(overview, title="[bold]Overview[/bold]", border_style="blue", padding=(1, 2)),
                    Panel(metrics_text, title="[bold green]Metrics[/bold green]", border_style="green", padding=(1, 2)),
                    Panel(
                        "\n".join(f"• {item}" for item in tasks) or "[dim]No active tasks.[/dim]",
                        title="[bold yellow]Tasks[/bold yellow]",
                        border_style="yellow",
                        padding=(1, 2),
                    ),
                    Panel(
                        "\n".join(f"• {item}" for item in issues) or "[dim]No unresolved issues.[/dim]",
                        title="[bold red]Issues[/bold red]",
                        border_style="red",
                        padding=(1, 2),
                    ),
                    Panel(
                        "\n".join(f"• {item}" for item in active_files) or "[dim]No active files.[/dim]",
                        title="[bold magenta]Active Files[/bold magenta]",
                        border_style="magenta",
                        padding=(1, 2),
                    ),
                ],
                expand=True,
                equal=True,
            )
            console.print(Panel(cols, title="[bold]Snapshot Details[/bold]", border_style="cyan", padding=1))
        elif format_type == "snapshot_compare":
            from rich.columns import Columns

            header = Table.grid(padding=1)
            header.add_column(style="bold cyan", justify="right")
            header.add_column(style="white")
            header.add_row("From:", str(payload.get("from_snapshot_id", "")))
            header.add_row("To:", str(payload.get("to_snapshot_id", "")))
            goals_text = f"{payload.get('from_goal', '') or 'None'} -> {payload.get('to_goal', '') or 'None'}"
            header.add_row("Goals:", goals_text)
            header.add_row(
                "Branches:",
                f"{payload.get('from_branch', '') or 'unknown'} -> {payload.get('to_branch', '') or 'unknown'}",
            )
            header.add_row("Summary:", str(payload.get("summary", "")))

            metric_deltas = payload.get("metric_deltas", {})
            metrics_text = "\n".join(f"• {key}: {value:+g}" for key, value in metric_deltas.items())
            if not metrics_text:
                metrics_text = "[dim]No metric deltas.[/dim]"

            def _bullet_block(key: str, empty_text: str) -> str:
                values = payload.get(key, [])
                return "\n".join(f"• {item}" for item in values) if values else f"[dim]{empty_text}[/dim]"

            cols = Columns(
                [
                    Panel(header, title="[bold]Overview[/bold]", border_style="blue", padding=(1, 2)),
                    Panel(
                        metrics_text,
                        title="[bold green]Metric Deltas[/bold green]",
                        border_style="green",
                        padding=(1, 2),
                    ),
                    Panel(
                        _bullet_block("changed_tracked_files", "No tracked file changes."),
                        title="[bold magenta]Changed Files[/bold magenta]",
                        border_style="magenta",
                        padding=(1, 2),
                    ),
                    Panel(
                        _bullet_block("added_tasks", "No new tasks.")
                        + "\n\n"
                        + _bullet_block("removed_tasks", "No removed tasks."),
                        title="[bold yellow]Task Changes[/bold yellow]",
                        border_style="yellow",
                        padding=(1, 2),
                    ),
                    Panel(
                        _bullet_block("added_issues", "No new issues.")
                        + "\n\n"
                        + _bullet_block("removed_issues", "No removed issues."),
                        title="[bold red]Issue Changes[/bold red]",
                        border_style="red",
                        padding=(1, 2),
                    ),
                ],
                expand=True,
                equal=True,
            )
            console.print(Panel(cols, title="[bold]Snapshot Comparison[/bold]", border_style="cyan", padding=1))
        elif format_type == "diff_summary":
            diffs = payload.get("diff_summary", [])
            if not diffs:
                console.print("[dim]No recent diffs. Workspace is clean.[/dim]")
            else:
                from rich.syntax import Syntax

                text = "\n".join(f"- {d}" for d in diffs)
                console.print(
                    Panel(
                        Syntax(text, "markdown"),
                        title="[bold]Workspace Uncommitted Changes[/bold]",
                        border_style="yellow",
                        padding=(1, 2),
                    )
                )
        else:
            console.print(_format_dict(payload, "Result"))
    elif isinstance(payload, list):
        if format_type == "decisions":
            if not payload:
                console.print("[dim]No decisions found.[/dim]")
            else:
                table = Table(show_header=True, header_style="bold blue")
                table.add_column("ID", style="cyan")
                table.add_column("Summary")
                table.add_column("Date")
                for item in payload:
                    created_str = (
                        item.get("created_at", "")[:10] if isinstance(item, dict) and "created_at" in item else ""
                    )
                    table.add_row(item.get("id", ""), item.get("summary", ""), created_str)
                console.print(Panel(table, title="[bold]Recent Decisions[/bold]", expand=False, border_style="blue"))
        elif format_type == "snapshots":
            if not payload:
                console.print("[dim]No snapshots found.[/dim]")
            else:
                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("ID", style="magenta")
                table.add_column("Created")
                table.add_column("Goal")
                table.add_column("Branch")
                table.add_column("Files", justify="right")
                table.add_column("Tasks", justify="right")
                for item in payload:
                    table.add_row(
                        item.get("id", ""),
                        item.get("created_at", "")[:19],
                        item.get("developer_goal", "") or "None",
                        item.get("branch", ""),
                        str(item.get("file_count", 0)),
                        str(item.get("active_task_count", 0)),
                    )
                console.print(Panel(table, title="[bold]Snapshot History[/bold]", expand=False, border_style="cyan"))
        elif format_type == "pins":
            if not payload:
                console.print("[dim]No pins found.[/dim]")
            else:
                table = Table(show_header=True, header_style="bold yellow")
                table.add_column("Path", style="cyan")
                table.add_column("Note")
                table.add_column("Created")
                for item in payload:
                    table.add_row(
                        item.get("path", ""),
                        item.get("note", ""),
                        item.get("created_at", "")[:19],
                    )
                console.print(Panel(table, title="[bold]Pinned Context[/bold]", expand=False, border_style="yellow"))
        elif format_type == "search":
            if not payload:
                console.print("[dim]No results found.[/dim]")
            else:
                for idx, item in enumerate(payload):
                    title_str = (
                        f"[bold cyan]Result {idx + 1}[/bold cyan] | {item.get('source', '')} - {item.get('key', '')}"
                    )
                    p = Panel(
                        item.get("snippet", ""),
                        title=title_str,
                        border_style="magenta",
                    )
                    console.print(p)
        elif format_type == "model_profiles":
            if not payload:
                console.print("[dim]No model profiles found.[/dim]")
            else:
                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("Model")
                table.add_column("Digest")
                table.add_column("Identity")
                table.add_column("Operational", justify="right")
                table.add_column("Calibration")
                for item in payload:
                    identity = item.get("model_identity", {})
                    table.add_row(
                        str(identity.get("model_name", "")),
                        str(identity.get("model_digest") or "unverified"),
                        str(identity.get("identity_strength", "")),
                        str(item.get("operational_context_tokens", "")),
                        str(item.get("calibration_status", "")),
                    )
                console.print(Panel(table, title="[bold]Model Profiles[/bold]", expand=False))
        elif format_type == "context_manifests":
            if not payload:
                console.print("[dim]No context manifests found.[/dim]")
            else:
                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("Manifest")
                table.add_column("Model")
                table.add_column("Decision")
                table.add_column("Included", justify="right")
                table.add_column("Remaining", justify="right")
                for item in payload:
                    table.add_row(
                        str(item.get("manifest_id", "")),
                        str(item.get("model", "")),
                        str(item.get("decision", "")),
                        str(item.get("included_tokens", 0)),
                        str(item.get("remaining_tokens", 0)),
                    )
                console.print(Panel(table, title="[bold]Context Manifests[/bold]", expand=False))
        elif format_type == "task_context_analyses":
            if not payload:
                console.print("[dim]No task-context analyses found.[/dim]")
            else:
                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("Analysis")
                table.add_column("Task")
                table.add_column("Decision")
                table.add_column("Packed", justify="right")
                table.add_column("Remaining", justify="right")
                table.add_column("Deficit", justify="right")
                table.add_column("Excluded", justify="right")
                table.add_column("Remediation")
                for item in payload:
                    remediation = item.get("remediation_actions", [])
                    table.add_row(
                        str(item.get("analysis_id", "")),
                        str(item.get("task_id", "")),
                        str(item.get("decision", "")),
                        str(item.get("packed_token_total", 0)),
                        str(item.get("remaining_input_tokens", 0)),
                        str(item.get("token_deficit", 0)),
                        str(len(item.get("excluded_candidates", []))),
                        "; ".join(str(action.get("message", "")) for action in remediation if isinstance(action, dict)),
                    )
                console.print(Panel(table, title="[bold]Task Context Analyses[/bold]", expand=False))
        else:
            for item in payload:
                console.print(item)
    else:
        console.print(payload)


@app.command()
def init(
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run_action(lambda: _service(project_root).init(), as_json=json, format_type="init")


@app.command()
def snapshot(
    goal: Annotated[str, typer.Option("--goal", help="Current developer goal")] = "",
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run_action(
        lambda: _service(project_root).snapshot(goal=goal).model_dump(mode="json"),
        as_json=json,
        format_type="snapshot",
        progress_message="Capturing project context...",
    )


@app.command("snapshots")
def snapshots_cmd(
    limit: Annotated[int, typer.Option("--limit")] = 20,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run_action(
        lambda: [item.model_dump(mode="json") for item in _service(project_root).snapshots_recent(limit)],
        as_json=json,
        format_type="snapshots",
    )


@app.command("show-snapshot")
def show_snapshot(
    snapshot_id: Annotated[str | None, typer.Option("--snapshot-id")] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run_action(
        lambda: _service(project_root).snapshot_details(snapshot_id=snapshot_id),
        as_json=json,
        format_type="snapshot_detail",
    )


@app.command("compare-snapshots")
def compare_snapshots(
    from_snapshot: Annotated[str | None, typer.Option("--from-snapshot")] = None,
    to_snapshot: Annotated[str | None, typer.Option("--to-snapshot")] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run_action(
        lambda: (
            _service(project_root)
            .compare_snapshots(
                from_snapshot_id=from_snapshot,
                to_snapshot_id=to_snapshot,
            )
            .model_dump(mode="json")
        ),
        as_json=json,
        format_type="snapshot_compare",
    )


@app.command()
def restore(
    snapshot_id: Annotated[str | None, typer.Option("--snapshot-id")] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run_action(
        lambda: _service(project_root).restore(snapshot_id=snapshot_id),
        as_json=json,
        format_type="restore",
        progress_message="Validating restore state...",
    )


@app.command("setup-agent")
def setup_agent(
    agent: Annotated[str, typer.Argument(help="choose from: cursor, claude, copilot, windsurf")],
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
) -> None:
    """Wire IDE AI agents directly to Infinite Context memory."""
    root = _effective_project_root(project_root)
    content = (
        "You are operating in a project managed by Infinite Context.\n"
        "To reliably understand the state of this repository, you MUST ALWAYS start by reading:\n"
        "`.infctx/agents/instructions.md`\n"
        "Do not guess context. Rely on the intelligent snapshot memory in `.infctx/agents/`.\n"
    )

    agent = agent.lower()
    if agent == "cursor":
        target = root / ".cursorrules"
    elif agent == "claude":
        target = root / "CLAUDE.md"
    elif agent == "windsurf":
        target = root / ".windsurfrules"
    elif agent == "copilot":
        target = root / ".github" / "copilot-instructions.md"
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        console.print(f"[red]Error:[/red] Unsupported agent '{agent}'.")
        raise typer.Exit(1)

    target.write_text(content, encoding="utf-8")
    console.print(f"[green]Successfully wired[/green] {agent} to Infinite Context via {target.relative_to(root)}")


@app.command()
def status(
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run_action(lambda: _service(project_root).status(), as_json=json, format_type="status")


@app.command()
def note(
    summary: Annotated[str, typer.Option("--summary")],
    rationale: Annotated[str, typer.Option("--rationale")],
    alternatives: Annotated[list[str] | None, typer.Option("--alternative")] = None,
    impact: Annotated[str, typer.Option("--impact")] = "",
    tags: Annotated[list[str] | None, typer.Option("--tag")] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
) -> None:
    decision_id = _run_action(
        lambda: _service(project_root).note(summary, rationale, alternatives or [], impact, tags or [])
    )
    console.print(Panel(f"Saved decision `{decision_id}`", border_style="green", expand=False))


@app.command()
def pin(
    path: Annotated[str, typer.Option("--path")],
    note: Annotated[str, typer.Option("--note")] = "",
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
) -> None:
    _run_action(lambda: _service(project_root).pin(path, note))
    console.print(Panel(f"Pinned `{path}`", border_style="green", expand=False))


@app.command()
def pins(
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run_action(
        lambda: [item.model_dump(mode="json") for item in _service(project_root).pin_records()],
        as_json=json,
        format_type="pins",
    )


@app.command()
def unpin(
    path: Annotated[str, typer.Option("--path")],
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
) -> None:
    removed = _run_action(lambda: _service(project_root).unpin(path), emit=False)
    if removed:
        console.print(Panel(f"Removed pin `{path}`", border_style="green", expand=False))
        return
    _print_error(f"No pin exists for `{path}`.")
    raise typer.Exit(1)


@app.command("ingest-chat")
def ingest_chat(
    chat_file: Annotated[Path | None, typer.Option("--file")] = None,
    auto: Annotated[bool, typer.Option("--auto", help="Auto-discover from Cursor/Copilot/Claude")] = False,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not chat_file and not auto:
        _print_error("Must provide either `--file` or `--auto`.")
        raise typer.Exit(1)

    svc = _service(project_root)
    if auto:
        from infinitecontex.capture.chat_auto_discover import auto_ingest_chat

        context = _run_action(
            auto_ingest_chat,
            progress_message="Scanning local AI chat sources...",
            emit=False,
        )
        if not isinstance(context, dict):
            _print_error("Auto-discovery did not return a usable payload.")
            raise typer.Exit(1)
        if context.get("selected_source") is None:
            console.print("[yellow]No local chat source could be discovered.[/yellow]")
            return

        persistable_keys = {
            "developer_goal",
            "decisions",
            "assumptions",
            "active_tasks",
            "unresolved_issues",
            "open_questions",
            "signal_sources",
            "selected_source",
            "selected_path",
            "checked_sources",
        }
        out = svc.ingest_chat_payload({key: context.get(key) for key in [*persistable_keys, "source_text"]})
        _emit(out, json, "ingest_chat")
    else:
        assert chat_file is not None
        _run_action(
            lambda: svc.ingest_chat(chat_file),
            as_json=json,
            format_type="ingest_chat",
            progress_message="Ingesting chat transcript...",
        )


@app.command("diff-summary")
def diff_summary(
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run_action(
        lambda: {"diff_summary": _service(project_root).diff_summary()},
        as_json=json,
        format_type="diff_summary",
    )


@app.command()
def decisions(
    limit: Annotated[int, typer.Option("--limit")] = 20,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run_action(lambda: _service(project_root).decisions_recent(limit), as_json=json, format_type="decisions")


@app.command()
def search(
    query: Annotated[str, typer.Option("--query")],
    limit: Annotated[int, typer.Option("--limit")] = 10,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run_action(lambda: _service(project_root).search(query, limit), as_json=json, format_type="search")


@app.command()
def prompt(
    mode: Annotated[PromptMode, typer.Option("--mode")] = PromptMode.GENERIC_AGENT_RESTORE,
    token_budget: Annotated[int, typer.Option("--token-budget")] = 1200,
    snapshot_id: Annotated[str | None, typer.Option("--snapshot-id")] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
) -> None:
    _run_action(
        lambda: _service(project_root).prompt(mode, token_budget, snapshot_id),
        progress_message="Compiling handoff prompt...",
    )


@app.command()
def export(
    output: Annotated[Path, typer.Option("--output")],
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
) -> None:
    out = _run_action(lambda: _service(project_root).export(output), progress_message="Exporting local state...")
    console.print(Panel(f"Exported archive to `{out}`", border_style="green", expand=False))


@app.command("import")
def import_cmd(
    archive: Annotated[Path, typer.Option("--archive")],
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
) -> None:
    _run_action(lambda: _service(project_root).import_archive(archive), progress_message="Importing local state...")
    console.print(Panel("Import complete", border_style="green", expand=False))


@app.command()
def doctor(
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run_action(lambda: _service(project_root).doctor(), as_json=json, format_type="doctor")


@app.command()
def config(
    set_file: Annotated[Path | None, typer.Option("--set-file")] = None,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    svc = _service(project_root)
    root = _effective_project_root(project_root)
    if set_file:
        resolved_set_file = set_file
        if not resolved_set_file.is_absolute():
            candidate = (root / resolved_set_file).resolve()
            if candidate.exists():
                resolved_set_file = candidate
        try:
            cfg = AppConfig.model_validate(orjson.loads(resolved_set_file.read_bytes()))
        except FileNotFoundError as exc:
            _print_error(f"The configuration file `{resolved_set_file}` was not found.")
            raise typer.Exit(1) from exc
        except Exception as exc:
            _print_error(f"Failed to load configuration: {exc}")
            raise typer.Exit(1) from exc
        _run_action(lambda: svc.config_set(cfg))
        console.print(Panel("Configuration updated successfully", border_style="green", expand=False))
        return

    _run_action(lambda: svc.config_get(), as_json=json, format_type="config")


@app.command()
def session(
    goal: Annotated[str, typer.Option("--goal", help="Goal used for structured session captures")] = "",
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    debounce_ms: Annotated[int, typer.Option("--debounce-ms")] = 1200,
    min_interval_sec: Annotated[int, typer.Option("--min-interval-sec")] = 3,
    once: Annotated[bool, typer.Option("--once", help="Capture the initial session snapshot and exit")] = False,
    json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    svc = _service(project_root)
    root = _effective_project_root(project_root)
    cfg = load_app_config(root)
    svc.init()

    initial_snapshot = _run_action(
        lambda: svc.snapshot(goal=goal),
        progress_message="Starting structured session...",
        emit=False,
    )
    if not hasattr(initial_snapshot, "id"):
        _print_error("Failed to create the initial session snapshot.")
        raise typer.Exit(1)

    session_payload = {
        "project_root": str(root),
        "goal": getattr(initial_snapshot, "intent").developer_goal or goal,
        "snapshot_id": getattr(initial_snapshot, "id"),
        "changed_paths": [],
        "mode": "once" if once else "live",
    }
    if once:
        _emit(session_payload, json, "session")
        return
    if json:
        _print_error("`session --json` is only supported together with `--once`.")
        raise typer.Exit(1)

    import datetime

    from rich.align import Align

    exclude_patterns = list(dict.fromkeys([*cfg.exclude_patterns, ".infctx/**"]))
    session_goal = getattr(initial_snapshot, "intent").developer_goal or goal or "None"
    last_snapshot_id = getattr(initial_snapshot, "id")
    last_snapshot_ts = time.time()
    last_trigger = "initial snapshot"
    last_changed_paths: list[str] = []
    skipped_batches = 0

    def generate_dashboard() -> Panel:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="cyan", justify="right")
        table.add_column(style="white")
        table.add_row("Root:", str(root))
        table.add_row("Goal:", f"[bold green]{session_goal}[/bold green]")
        table.add_row("Last Snapshot:", f"[bold magenta]{last_snapshot_id}[/bold magenta]")
        table.add_row("Last Trigger:", last_trigger)
        table.add_row("Skipped:", str(skipped_batches))
        table.add_row("Recent Changes:", "\n".join(last_changed_paths) if last_changed_paths else "[dim]Waiting[/dim]")
        table.add_row("Status:", Spinner("dots", text="[yellow]Watching filtered project changes...[/yellow]"))
        return Panel(
            Align.center(table),
            title=f"[bold]Infinite Context Session[/bold] • {datetime.datetime.now().strftime('%H:%M:%S')}",
            border_style="cyan",
        )

    with Live(generate_dashboard(), refresh_per_second=4) as live:
        for changes in watch(root, debounce=debounce_ms):
            relevant_changes = _filter_watch_changes(changes, root, exclude_patterns)
            if not relevant_changes:
                continue
            now = time.time()
            if now - last_snapshot_ts < min_interval_sec:
                skipped_batches += 1
                last_trigger = "cooldown skip"
                last_changed_paths = relevant_changes
                live.update(generate_dashboard())
                continue

            try:
                snap = svc.snapshot(goal=goal)
            except Exception as exc:
                skipped_batches += 1
                last_trigger = f"snapshot failed: {exc}"
                last_changed_paths = relevant_changes
                live.update(generate_dashboard())
                continue
            last_snapshot_ts = now
            last_snapshot_id = snap.id
            last_trigger = "file changes"
            last_changed_paths = relevant_changes
            live.update(generate_dashboard())


@app.command("watch")
def watch_loop(
    goal: Annotated[str, typer.Option("--goal", help="Goal used for auto snapshots")] = "",
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    debounce_ms: Annotated[int, typer.Option("--debounce-ms")] = 1200,
    min_interval_sec: Annotated[int, typer.Option("--min-interval-sec")] = 3,
) -> None:
    session(
        goal=goal,
        project_root=project_root,
        debounce_ms=debounce_ms,
        min_interval_sec=min_interval_sec,
        once=False,
        json=False,
    )


@app.command("cleanup")
def cleanup(
    keep: Annotated[int, typer.Option("--keep", help="Number of recent snapshots to keep")] = 10,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm deletion of old snapshots")] = False,
) -> None:
    """Prune old snapshots and compact the local memory database."""
    svc = _service(project_root)
    rows = svc.db.query("SELECT id FROM snapshots ORDER BY created_at DESC")
    if len(rows) <= keep:
        console.print(Panel(f"Only {len(rows)} snapshots exist. Kept all.", border_style="green", expand=False))
        return

    to_delete = [str(r["id"]) for r in rows[keep:]]
    if not yes:
        _print_error(f"Cleanup would remove {len(to_delete)} snapshots. Re-run with `--yes` to confirm.")
        raise typer.Exit(1)
    for snap_id in to_delete:
        svc.db.execute("DELETE FROM snapshots WHERE id = ?", (snap_id,))
        snap_file = svc.layout.snapshots / f"{snap_id}.json"
        if snap_file.exists():
            snap_file.unlink()

    svc.db.execute("VACUUM")
    console.print(Panel(f"Removed {len(to_delete)} old snapshots and compacted the database.", border_style="green"))
