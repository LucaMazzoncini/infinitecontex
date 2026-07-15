from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from infinitecontex.context_budget.calculator import ContextBudgetCalculator
from infinitecontex.context_packing.store import ContextManifestStore
from infinitecontex.model_profiles.models import (
    CalibrationStatus,
    IdentityStrength,
    ModelIdentity,
    ModelProfile,
    ValueProvenance,
)
from infinitecontex.model_profiles.store import ModelProfileStore
from infinitecontex.planning.models import PlanInput
from infinitecontex.planning.service import PlanningService
from infinitecontex.planning.store import PlanStore
from infinitecontex.task_context.candidates import (
    TaskContextCandidateBuilder,
    path_reference_from_string,
    task_path_references,
    task_symbol_references,
)
from infinitecontex.task_context.errors import TaskContextPersistenceError, TaskContextProfileError
from infinitecontex.task_context.models import (
    InventoryClassification,
    InventoryEntry,
    PathReference,
    PathReferenceKind,
    PathResolutionOutcome,
    ReferenceRequirement,
    ScopeConflictKind,
    SymbolKind,
    SymbolReference,
    SymbolResolutionOutcome,
    TaskContextDecision,
)
from infinitecontex.task_context.paths import PathResolver
from infinitecontex.task_context.policy import RepositoryResolutionPolicy
from infinitecontex.task_context.repository import (
    GitRepositoryState,
    GitStateProvider,
    RepositoryInventoryService,
)
from infinitecontex.task_context.scopes import validate_task_scopes
from infinitecontex.task_context.service import TaskContextService
from infinitecontex.task_context.store import TaskContextAnalysisStore
from infinitecontex.task_context.symbols import PythonAstSymbolResolver, SymbolResolutionService

NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


class FakeGit(GitStateProvider):
    def __init__(self, state: GitRepositoryState) -> None:
        self.state = state

    def inspect(self, root: Path) -> GitRepositoryState:
        return self.state


def _git(*paths: str, **updates: object) -> GitRepositoryState:
    values: dict[str, object] = {
        "available": True,
        "commit_hash": "a" * 40,
        "branch": "main",
        "remote_identity": "https://example.invalid/repo.git",
        "tracked_paths": paths,
    }
    values.update(updates)
    return GitRepositoryState(**values)  # type: ignore[arg-type]


def _write_repository(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / ".infctx").mkdir()
    (root / "src" / "sample.py").write_text(
        "CONST = 3\n\nclass Worker:\n    @property\n    def name(self):\n        return 'worker'\n\n"
        "    def run(self):\n        return CONST\n\nasync def fetch():\n    return 1\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_sample.py").write_text(
        "class TestWorker:\n    pass\n\ndef test_worker():\n    assert True\n",
        encoding="utf-8",
    )
    (root / "docs" / "guide.md").write_text("# Guide\n\nSynthetic documentation.\n", encoding="utf-8")
    (root / ".infctx" / "private.json").write_text("{}", encoding="utf-8")


def _inventory(root: Path, *, now: datetime = NOW):
    state = _git("docs/guide.md", "src/sample.py", "tests/test_sample.py")
    return RepositoryInventoryService(git_provider=FakeGit(state), clock=lambda: now).build(root)


def _plan_input(*, files: list[str] | None = None, symbols: list[str] | None = None, **task_updates: object):
    task: dict[str, object] = {
        "task_key": "inspect",
        "title": "Inspect explicit declarations",
        "objective": "Resolve only the declared source context.",
        "description": "Read-only deterministic inspection.",
        "task_type": "analysis",
        "status": "ready",
        "provenance": "human_authored",
        "context_requirements": {
            "required_files": files or [],
            "required_symbols": symbols or [],
        },
    }
    task.update(task_updates)
    return PlanInput.model_validate(
        {
            "stable_plan_key": "task-context-test",
            "title": "Task context test",
            "objective": "Validate explicit repository declarations.",
            "planner_provenance": "human_authored",
            "repository_ref": "synthetic-repository",
            "tasks": [task],
        }
    )


def _profile(
    *,
    digest: str | None = "sha256:test",
    maximum: int = 4000,
    strategy: str = "conservative-mixed-text-v2",
    calibration: CalibrationStatus = CalibrationStatus.UNCALIBRATED,
) -> ModelProfile:
    operational = maximum + 400
    estimated = ValueProvenance.ESTIMATED
    return ModelProfile(
        profile_id=f"profile-task-context-{digest or 'weak'}-{maximum}",
        model_identity=ModelIdentity(
            provider="ollama",
            model_name="demo:latest",
            normalized_model_name="demo:latest",
            model_digest=digest,
            identity_strength=IdentityStrength.VERIFIED if digest else IdentityStrength.WEAK,
            inspected_at=NOW,
        ),
        advertised_context_tokens=1_000_000,
        configured_context_tokens=operational,
        operational_context_tokens=operational,
        reserved_output_tokens=100,
        reserved_tool_result_tokens=100,
        reserved_system_prompt_tokens=100,
        safety_margin_tokens=100,
        maximum_recommended_input_tokens=maximum,
        tokenizer_strategy="unverified",
        token_estimation_strategy=strategy,
        calibration_status=calibration,
        created_at=NOW,
        updated_at=NOW,
        provenance={
            name: estimated
            for name in (
                "advertised_context_tokens",
                "configured_context_tokens",
                "operational_context_tokens",
                "reserved_output_tokens",
                "reserved_tool_result_tokens",
                "reserved_system_prompt_tokens",
                "safety_margin_tokens",
                "maximum_recommended_input_tokens",
                "tokenizer_strategy",
                "token_estimation_strategy",
            )
        },
    )


def _service(root: Path, plan: PlanInput, profile: ModelProfile | None = None) -> tuple[TaskContextService, str]:
    state = _git("docs/guide.md", "src/sample.py", "tests/test_sample.py")
    plans = PlanStore(root / ".infctx" / "plans")
    revision, _, _ = PlanningService(plans, clock=lambda: NOW).import_plan(plan)
    profiles = ModelProfileStore(root / ".infctx" / "model-profiles")
    if profile is not None:
        profiles.save(profile)
    service = TaskContextService(
        plans,
        profiles,
        ContextBudgetCalculator(),
        RepositoryInventoryService(git_provider=FakeGit(state), clock=lambda: NOW),
        TaskContextAnalysisStore(root / ".infctx" / "plans"),
        ContextManifestStore(root / ".infctx" / "context-manifests"),
        clock=lambda: NOW,
    )
    return service, revision.plan_id


def test_inventory_snapshot_is_deterministic_and_tracks_git_state(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    state = _git(
        "src/sample.py",
        "docs/guide.md",
        staged_paths=("src/sample.py",),
        modified_paths=("src/sample.py",),
        untracked_paths=("tests/test_sample.py", ".infctx/private.json"),
    )
    first = RepositoryInventoryService(git_provider=FakeGit(state), clock=lambda: NOW).build(tmp_path)
    second = RepositoryInventoryService(git_provider=FakeGit(state), clock=lambda: NOW + timedelta(hours=1)).build(
        tmp_path
    )
    assert [entry.path for entry in first.entries] == ["docs/guide.md", "src/sample.py", "tests/test_sample.py"]
    assert first.snapshot.dirty and first.snapshot.staged_paths == ("src/sample.py",)
    assert first.snapshot.semantic_fingerprint == second.snapshot.semantic_fingerprint
    assert first.snapshot.created_at != second.snapshot.created_at
    (tmp_path / "src" / "sample.py").write_text("changed = True\n", encoding="utf-8")
    changed = RepositoryInventoryService(git_provider=FakeGit(state), clock=lambda: NOW).build(tmp_path)
    assert changed.snapshot.semantic_fingerprint != first.snapshot.semantic_fingerprint


def test_inventory_classifies_binary_large_missing_and_unsafe_symlink(tmp_path: Path) -> None:
    (tmp_path / "binary.bin").write_bytes(b"x\0y")
    (tmp_path / "large.txt").write_text("x" * 20, encoding="utf-8")
    state = _git("binary.bin", "large.txt", "missing.txt")
    policy = RepositoryResolutionPolicy(maximum_source_file_bytes=10)
    inventory = RepositoryInventoryService(policy, FakeGit(state), clock=lambda: NOW).build(tmp_path)
    values = {item.path: item.classification for item in inventory.entries}
    assert values == {
        "binary.bin": InventoryClassification.BINARY,
        "large.txt": InventoryClassification.TOO_LARGE,
        "missing.txt": InventoryClassification.MISSING,
    }


@pytest.mark.parametrize("value", ["../secret", "C:relative", "\\\\server\\share", "\\\\?\\C:\\x", "a\0b"])
def test_path_normalization_rejects_escape_devices_network_and_controls(tmp_path: Path, value: str) -> None:
    _write_repository(tmp_path)
    resolver = PathResolver(tmp_path, _inventory(tmp_path))
    normalized, error = resolver.normalize(value, PathReferenceKind.EXACT_FILE)
    assert normalized is None and error


def test_windows_and_posix_paths_ranges_globs_hashes_and_case_ambiguity(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    inventory = _inventory(tmp_path)
    resolver = PathResolver(tmp_path, inventory)
    task_id = "task-" + "a" * 24
    windows = resolver.resolve(path_reference_from_string("src\\sample.py", task_id, "required_files"))
    posix = resolver.resolve(path_reference_from_string("src/sample.py", task_id, "required_files"))
    assert windows.resolution.matches == posix.resolution.matches
    ranged = resolver.resolve(path_reference_from_string("src/sample.py#L1-L3", task_id, "required_files"))
    assert ranged.resolution.outcome == PathResolutionOutcome.RESOLVED
    assert ranged.sources[0].content.startswith("CONST")
    globbed = resolver.resolve(
        PathReference(
            original_value="**/*.py",
            kind=PathReferenceKind.GLOB,
            source_task_id=task_id,
            source_field="required_files",
        )
    )
    assert globbed.resolution.outcome == PathResolutionOutcome.RESOLVED_MULTIPLE
    mismatch = resolver.resolve(
        PathReference(
            original_value="src/sample.py",
            expected_content_hash="0" * 64,
            source_task_id=task_id,
            source_field="required_files",
        )
    )
    assert mismatch.resolution.outcome == PathResolutionOutcome.HASH_MISMATCH


def test_python_ast_symbols_resolve_without_import_and_report_ambiguity(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    (tmp_path / "tests" / "test_sample.py").write_text(
        "def fetch():\n    raise RuntimeError('must not run')\n", encoding="utf-8"
    )
    inventory = _inventory(tmp_path)
    path_resolver = PathResolver(tmp_path, inventory)
    resolver = SymbolResolutionService((PythonAstSymbolResolver(tmp_path, inventory, path_resolver),))
    task_id = "task-" + "a" * 24
    worker = resolver.resolve(
        SymbolReference(
            original_reference="src/sample.py::Worker.run",
            language="python",
            symbol_name="run",
            qualified_name="Worker.run",
            file_hint="src/sample.py",
            symbol_kind=SymbolKind.METHOD,
            source_task_id=task_id,
            source_field="required_symbols",
        )
    )
    assert worker.resolution.outcome == SymbolResolutionOutcome.RESOLVED_WITH_FILE_HINT
    assert worker.resolution.matches[0].qualified_name.endswith("Worker.run")
    ambiguous = resolver.resolve(
        SymbolReference(
            original_reference="fetch",
            language="python",
            symbol_name="fetch",
            source_task_id=task_id,
            source_field="required_symbols",
        )
    )
    assert ambiguous.resolution.outcome == SymbolResolutionOutcome.AMBIGUOUS
    unsupported = resolver.resolve(
        SymbolReference(
            original_reference="Game.Player",
            language="csharp",
            symbol_name="Player",
            source_task_id=task_id,
            source_field="required_symbols",
        )
    )
    assert unsupported.resolution.outcome == SymbolResolutionOutcome.UNSUPPORTED_LANGUAGE


def test_scope_conflicts_are_deterministic_and_do_not_grant_capabilities(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    plan = _plan_input(
        files=["src/sample.py"],
        affected_scopes=["src/**"],
        forbidden_scopes=["src/sample.py"],
        requested_capabilities=["write_source_files"],
    )
    revision, _ = PlanningService(PlanStore(tmp_path / "plans"), clock=lambda: NOW).validate(plan)
    inventory = _inventory(tmp_path)
    resolver = PathResolver(tmp_path, inventory)
    paths = tuple(resolver.resolve(ref).resolution for ref in ())
    conflicts = validate_task_scopes(revision.tasks[0], inventory, paths, RepositoryResolutionPolicy())
    assert conflicts[0].kind in {ScopeConflictKind.AFFECTED_UNDER_FORBIDDEN, ScopeConflictKind.EXACT_OVERLAP}
    assert revision.tasks[0].granted_capabilities == ()


def test_context_fit_reuses_packer_persists_and_becomes_stale(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    service, plan_id = _service(
        tmp_path,
        _plan_input(files=["src/sample.py", "docs/guide.md"], symbols=["src/sample.py::Worker.run"]),
        _profile(),
    )
    first = service.context_fit(plan_id, tmp_path, model_name="demo:latest", digest="sha256:test")[0]
    second = service.context_fit(plan_id, tmp_path, model_name="demo:latest", digest="sha256:test")[0]
    assert first.passing and first.decision == TaskContextDecision.FITS_TARGET
    assert first.semantic_fingerprint == second.semantic_fingerprint
    assert first.analysis_id == second.analysis_id
    loaded = service.analysis_store.load_current(plan_id, 1, first.task_id)
    assert loaded == first
    assert not service.staleness(first, tmp_path).stale
    (tmp_path / "src" / "sample.py").write_text("CONST = 4\n", encoding="utf-8")
    assert service.staleness(first, tmp_path).stale
    raw = service.analysis_store.serialize(first)
    assert b"return CONST" not in raw


def test_required_unresolved_and_unsupported_symbol_fail_optional_warns(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    service, plan_id = _service(tmp_path, _plan_input(files=["missing.py"]), _profile())
    analysis = service.context_fit(plan_id, tmp_path, model_name="demo:latest", digest="sha256:test")[0]
    assert not analysis.passing
    assert analysis.decision == TaskContextDecision.REQUIRED_REFERENCE_UNRESOLVED
    task_id = analysis.task_id
    optional = PathReference(
        original_value="also-missing.py",
        requirement=ReferenceRequirement.OPTIONAL,
        source_task_id=task_id,
        source_field="optional_files",
    )
    with_optional = service.context_fit(
        plan_id,
        tmp_path,
        model_name="demo:latest",
        digest="sha256:test",
        additional_path_references={task_id: (optional,)},
    )[0]
    assert any("Optional path" in warning for warning in with_optional.warnings)

    csharp_service, csharp_plan = _service(
        tmp_path / "csharp",
        _plan_input(symbols=["Player"]),
        _profile(),
    )
    # Explicit structured references carry the unsupported language without pretending text search is semantic.
    csharp_task = csharp_service.plan_store.load_current(csharp_plan).tasks[0]
    required_csharp = SymbolReference(
        original_reference="Player",
        language="csharp",
        symbol_name="Player",
        source_task_id=csharp_task.task_id,
        source_field="required_symbols",
    )
    unsupported = csharp_service.context_fit(
        csharp_plan,
        tmp_path,
        model_name="demo:latest",
        digest="sha256:test",
        additional_symbol_references={csharp_task.task_id: (required_csharp,)},
    )[0]
    assert unsupported.decision == TaskContextDecision.UNSUPPORTED_REQUIRED_SYMBOL


def test_profile_digest_weak_identity_and_advertised_capacity_are_not_budget(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    service, plan_id = _service(tmp_path, _plan_input(), _profile())
    with pytest.raises(TaskContextProfileError, match="digest"):
        service.context_fit(plan_id, tmp_path, model_name="demo:latest", digest="sha256:other")
    weak_service, weak_plan = _service(tmp_path / "weak", _plan_input(), _profile(digest=None))
    with pytest.raises(TaskContextProfileError, match="weak identity"):
        weak_service.context_fit(weak_plan, tmp_path, model_name="demo:latest", digest=None)
    analysis = service.context_fit(plan_id, tmp_path, model_name="demo:latest", digest="sha256:test")[0]
    assert analysis.maximum_recommended_input_tokens == 4000
    assert analysis.operational_context_tokens == 4400


def test_exact_task_context_boundary_passes_and_one_token_overflow_requires_split(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _write_repository(repository)
    plan = _plan_input()
    baseline_service, baseline_plan = _service(tmp_path / "baseline", plan, _profile(maximum=4000))
    baseline = baseline_service.context_fit(
        baseline_plan,
        repository,
        model_name="demo:latest",
        digest="sha256:test",
    )[0]
    exact_service, exact_plan = _service(
        tmp_path / "exact",
        plan,
        _profile(maximum=baseline.mandatory_token_total),
    )
    exact = exact_service.context_fit(
        exact_plan,
        repository,
        model_name="demo:latest",
        digest="sha256:test",
    )[0]
    overflow_service, overflow_plan = _service(
        tmp_path / "overflow",
        plan,
        _profile(maximum=baseline.mandatory_token_total - 1),
    )
    overflow = overflow_service.context_fit(
        overflow_plan,
        repository,
        model_name="demo:latest",
        digest="sha256:test",
    )[0]
    assert exact.passing and exact.remaining_input_tokens == 0
    assert overflow.decision == TaskContextDecision.SPLIT_REQUIRED
    assert overflow.token_deficit == 1
    assert {item.code for item in overflow.remediation_actions} == {"narrow_whole_file", "split_task"}


def test_legacy_and_v2_estimators_work_but_stale_profile_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _write_repository(repository)
    for index, strategy in enumerate(("normalized-utf8-byte-upper-bound-v1", "conservative-mixed-text-v2")):
        service, plan_id = _service(
            tmp_path / f"strategy-{index}",
            _plan_input(files=["src/sample.py"]),
            _profile(strategy=strategy),
        )
        analysis = service.context_fit(
            plan_id,
            repository,
            model_name="demo:latest",
            digest="sha256:test",
        )[0]
        assert analysis.estimator_strategy == strategy and analysis.passing
    stale_service, stale_plan = _service(
        tmp_path / "stale",
        _plan_input(),
        _profile(calibration=CalibrationStatus.STALE),
    )
    with pytest.raises(TaskContextProfileError, match="stale"):
        stale_service.context_fit(
            stale_plan,
            repository,
            model_name="demo:latest",
            digest="sha256:test",
        )


def test_malformed_analysis_fails_closed_and_existing_infctx_data_survives(tmp_path: Path) -> None:
    marker = tmp_path / ".infctx" / "keep.txt"
    marker.parent.mkdir()
    marker.write_text("keep", encoding="utf-8")
    store = TaskContextAnalysisStore(tmp_path / ".infctx" / "plans")
    plan_id = "plan-" + "a" * 24
    directory = store.plans_directory / plan_id / "analyses" / "000001" / "task-context"
    directory.mkdir(parents=True)
    (directory / ("task-context-" + "b" * 24 + ".json")).write_text("not-json", encoding="utf-8")
    with pytest.raises(TaskContextPersistenceError, match="malformed"):
        store.list(plan_id, 1)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_ten_thousand_file_inventory_is_stable_under_reversed_input(tmp_path: Path) -> None:
    entries = tuple(
        InventoryEntry(
            path=f"src/generated/{index:05d}.py",
            size_bytes=1,
            content_hash=hashlib.sha256(str(index).encode()).hexdigest(),
            classification=InventoryClassification.TEXT,
            tracked=True,
        )
        for index in range(10_000)
    )
    service = RepositoryInventoryService(clock=lambda: NOW)
    first = service.from_entries(tmp_path, entries, _git(*(item.path for item in entries)), created_at=NOW)
    second = service.from_entries(tmp_path, reversed(entries), _git(*(item.path for item in entries)), created_at=NOW)
    assert first.snapshot.file_count == 10_000
    assert first.snapshot.semantic_fingerprint == second.snapshot.semantic_fingerprint
    assert first.entries[0].path == "src/generated/00000.py"


def test_ten_thousand_sparse_tasks_resolve_without_recursion(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    tasks = [
        {
            "task_key": f"task-{index:05d}",
            "title": f"Task {index}",
            "objective": "Inspect no implicit repository context.",
            "task_type": "analysis",
            "status": "draft",
            "provenance": "human_authored",
        }
        for index in range(10_000)
    ]
    plan = PlanInput.model_validate(
        {
            "stable_plan_key": "ten-thousand-context-tasks",
            "title": "Large sparse task context plan",
            "objective": "Validate deterministic sparse task resolution.",
            "planner_provenance": "human_authored",
            "repository_ref": "synthetic-repository",
            "tasks": tasks,
        }
    )
    plans = PlanStore(tmp_path / ".infctx" / "plans")
    revision, _, _ = PlanningService(plans, clock=lambda: NOW).import_plan(plan)
    resolver = TaskContextService(
        plans,
        ModelProfileStore(tmp_path / ".infctx" / "model-profiles"),
        ContextBudgetCalculator(),
        RepositoryInventoryService(git_provider=FakeGit(_git()), clock=lambda: NOW),
        TaskContextAnalysisStore(tmp_path / ".infctx" / "plans"),
        clock=lambda: NOW,
    )
    loaded, inventory, reports = resolver.resolve_plan(revision.plan_id, repository)
    assert loaded.task_count == len(reports) == 10_000
    assert inventory.snapshot.file_count == 0
    assert tuple(item.task_id for item in reports) == tuple(sorted(item.task_id for item in reports))


def test_persisted_structured_and_optional_declarations_are_typed_and_order_stable(tmp_path: Path) -> None:
    context = {
        "optional_files": ["docs/guide.md"],
        "optional_symbols": ["src/sample.py::fetch"],
        "path_references": [
            {"value": "src/sample.py", "kind": "source_range", "line_start": 1, "line_end": 1},
            {"value": "tests/**", "kind": "glob", "requirement": "forbidden"},
        ],
        "symbol_references": [
            {
                "reference": "Player.Run",
                "language": "csharp",
                "symbol_name": "Run",
                "qualified_name": "Player.Run",
                "file_hint": "Player.cs",
                "symbol_kind": "method",
                "requirement": "optional",
            }
        ],
    }
    plan = _plan_input(context_requirements=context)
    revision, _ = PlanningService(PlanStore(tmp_path / "plans"), clock=lambda: NOW).validate(plan)
    task = revision.tasks[0]
    paths = task_path_references(task)
    symbols = task_symbol_references(task)
    assert {item.requirement for item in paths} == {
        ReferenceRequirement.REQUIRED,
        ReferenceRequirement.OPTIONAL,
        ReferenceRequirement.FORBIDDEN,
    }
    assert any(item.kind == PathReferenceKind.SOURCE_RANGE and item.line_start == 1 for item in paths)
    assert any(item.language == "csharp" and item.requirement == ReferenceRequirement.OPTIONAL for item in symbols)
    reversed_context = dict(context)
    reversed_context["path_references"] = list(reversed(context["path_references"]))
    reversed_plan = _plan_input(context_requirements=reversed_context)
    reversed_revision, _ = PlanningService(PlanStore(tmp_path / "other"), clock=lambda: NOW).validate(reversed_plan)
    assert task.task_fingerprint == reversed_revision.tasks[0].task_fingerprint


def test_git_ignored_and_post_inventory_changes_have_explicit_outcomes(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    (tmp_path / "ignored.log").write_text("ignored", encoding="utf-8")
    state = _git(
        "docs/guide.md",
        "src/sample.py",
        "tests/test_sample.py",
        ignored_paths=("ignored.log",),
    )
    inventory = RepositoryInventoryService(git_provider=FakeGit(state), clock=lambda: NOW).build(tmp_path)
    resolver = PathResolver(tmp_path, inventory)
    task_id = "task-" + "a" * 24
    ignored_result = resolver.resolve(
        PathReference(
            original_value="ignored.log",
            source_task_id=task_id,
            source_field="context_requirements.required_files",
        )
    )
    assert ignored_result.resolution.outcome == PathResolutionOutcome.IGNORED
    (tmp_path / "src" / "sample.py").write_text("CONST = 99\n", encoding="utf-8")
    stale = resolver.resolve(
        PathReference(
            original_value="src/sample.py",
            source_task_id=task_id,
            source_field="context_requirements.required_files",
        )
    )
    assert stale.resolution.outcome == PathResolutionOutcome.STALE_SNAPSHOT


@pytest.mark.parametrize(
    ("name", "kind", "file_hint"),
    [
        ("sample", SymbolKind.MODULE, "src/sample.py"),
        ("Worker", SymbolKind.CLASS, "src/sample.py"),
        ("Worker.name", SymbolKind.PROPERTY, "src/sample.py"),
        ("fetch", SymbolKind.FUNCTION, "src/sample.py"),
        ("CONST", SymbolKind.CONSTANT, "src/sample.py"),
        ("TestWorker", SymbolKind.TEST, "tests/test_sample.py"),
        ("test_worker", SymbolKind.TEST, "tests/test_sample.py"),
    ],
)
def test_python_symbol_kinds_have_frozen_spans(
    tmp_path: Path,
    name: str,
    kind: SymbolKind,
    file_hint: str,
) -> None:
    _write_repository(tmp_path)
    inventory = _inventory(tmp_path)
    path_resolver = PathResolver(tmp_path, inventory)
    resolver = PythonAstSymbolResolver(tmp_path, inventory, path_resolver)
    result = resolver.resolve(
        SymbolReference(
            original_reference=name,
            language="python",
            symbol_name=name.rsplit(".", 1)[-1],
            qualified_name=name if "." in name else None,
            file_hint=file_hint,
            symbol_kind=kind,
            source_task_id="task-" + "a" * 24,
            source_field="context_requirements.required_symbols",
        )
    )
    assert result.resolution.outcome == SymbolResolutionOutcome.RESOLVED_WITH_FILE_HINT
    match = result.resolution.matches[0]
    assert match.start_line <= match.end_line
    assert match.normalized_source_hash == result.sources[0].frozen_content_hash


def test_nested_duplicate_syntax_error_and_stale_python_symbols(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    (tmp_path / "src" / "nested.py").write_text(
        "def outer():\n    def inner():\n        return 1\n    return inner()\n\ndef outer():\n    return 2\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    state = _git("src/sample.py", "src/nested.py", "src/broken.py")
    inventory = RepositoryInventoryService(git_provider=FakeGit(state), clock=lambda: NOW).build(tmp_path)
    path_resolver = PathResolver(tmp_path, inventory)
    resolver = PythonAstSymbolResolver(tmp_path, inventory, path_resolver)
    task_id = "task-" + "a" * 24
    duplicate = resolver.resolve(
        SymbolReference(
            original_reference="outer",
            language="python",
            symbol_name="outer",
            file_hint="src/nested.py",
            source_task_id=task_id,
            source_field="required_symbols",
        )
    )
    assert duplicate.resolution.outcome == SymbolResolutionOutcome.AMBIGUOUS
    syntax = resolver.resolve(
        SymbolReference(
            original_reference="broken",
            language="python",
            symbol_name="broken",
            file_hint="src/broken.py",
            source_task_id=task_id,
            source_field="required_symbols",
        )
    )
    assert syntax.resolution.outcome == SymbolResolutionOutcome.SYNTAX_ERROR
    (tmp_path / "src" / "sample.py").write_text("def changed():\n    pass\n", encoding="utf-8")
    stale = resolver.resolve(
        SymbolReference(
            original_reference="changed",
            language="python",
            symbol_name="changed",
            file_hint="src/sample.py",
            source_task_id=task_id,
            source_field="required_symbols",
        )
    )
    assert stale.resolution.outcome == SymbolResolutionOutcome.STALE_CONTENT


def test_candidate_builder_uses_only_explicit_sources_and_is_deterministic(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    plan = _plan_input(
        context_requirements={
            "required_files": ["src/sample.py"],
            "optional_documentation": ["docs/guide.md"],
            "required_diagnostics": ["synthetic diagnostic"],
        }
    )
    revision, _ = PlanningService(PlanStore(tmp_path / "plans"), clock=lambda: NOW).validate(plan)
    task = revision.tasks[0]
    inventory = _inventory(tmp_path)
    resolver = PathResolver(tmp_path, inventory)
    resolved = tuple(resolver.resolve(item) for item in task_path_references(task))
    first = TaskContextCandidateBuilder().build(task, inventory, resolved, ())
    second = TaskContextCandidateBuilder().build(task, inventory, tuple(reversed(resolved)), ())
    assert tuple(item.candidate_id for item in first) == tuple(item.candidate_id for item in second)
    assert {item.source_path for item in first if item.source_path} == {"src/sample.py", "docs/guide.md"}
    assert any(item.mandatory and item.source_path == "src/sample.py" for item in first)
    assert any(not item.mandatory and item.source_path == "docs/guide.md" for item in first)
    assert all(item.source_path != "tests/test_sample.py" for item in first)


def test_scope_conflict_classes_and_order_are_deterministic(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    plan = _plan_input(
        files=["src/sample.py"],
        affected_scopes=["**", "src", "src/sample.py", "src/sample.py"],
        forbidden_scopes=["src/**"],
        expected_outputs=[{"name": "escape", "description": "Unsafe output", "reference": "../outside.py"}],
    )
    revision, _ = PlanningService(PlanStore(tmp_path / "plans"), clock=lambda: NOW).validate(plan)
    inventory = _inventory(tmp_path)
    resolver = PathResolver(tmp_path, inventory)
    paths = tuple(resolver.resolve(item).resolution for item in task_path_references(revision.tasks[0]))
    conflicts = validate_task_scopes(revision.tasks[0], inventory, paths, RepositoryResolutionPolicy())
    kinds = {item.kind for item in conflicts}
    assert {
        ScopeConflictKind.ROOT_WIDE_AFFECTED,
        ScopeConflictKind.DUPLICATE_SCOPE,
        ScopeConflictKind.REDUNDANT_SCOPE,
        ScopeConflictKind.AFFECTED_UNDER_FORBIDDEN,
        ScopeConflictKind.REQUIRED_FILE_FORBIDDEN,
        ScopeConflictKind.OUTPUT_ESCAPE,
    }.issubset(kinds)
    assert conflicts == tuple(
        sorted(conflicts, key=lambda item: (item.kind, item.left, item.right or "", item.message))
    )


def test_optional_context_exclusion_and_plan_profile_reference(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _write_repository(repository)
    plan = _plan_input(
        context_requirements={
            "optional_documentation": ["docs/guide.md"],
            "required_diagnostics": ["mandatory diagnostic"],
        }
    )
    roomy, roomy_id = _service(tmp_path / "roomy", plan, _profile(maximum=4000))
    baseline = roomy.context_fit(
        roomy_id,
        repository,
        model_name="demo:latest",
        digest="sha256:test",
    )[0]
    profile = _profile(maximum=baseline.mandatory_token_total)
    pinned = plan.model_copy(update={"model_profile_ref": profile.profile_id})
    constrained, constrained_id = _service(tmp_path / "constrained", pinned, profile)
    analysis = constrained.context_fit(constrained_id, repository)[0]
    assert analysis.passing and analysis.decision == TaskContextDecision.FITS_HARD_LIMIT
    assert analysis.optional_token_total > 0
    assert analysis.excluded_candidates
