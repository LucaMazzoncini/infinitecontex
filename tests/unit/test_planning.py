from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import orjson
import pytest
from pydantic import ValidationError

from infinitecontex.planning.errors import (
    PlanFormatError,
    PlanNotFoundError,
    PlanPersistenceError,
    PlanRevisionNotFoundError,
    PlanTransitionError,
    PlanValidationError,
)
from infinitecontex.planning.models import PlanInput, PlannerOutputEnvelope, PlannerProvenance, TaskStatus
from infinitecontex.planning.normalization import revision_fingerprint
from infinitecontex.planning.service import PlanningService
from infinitecontex.planning.store import PlanStore
from infinitecontex.planning.transitions import validate_transition

NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


def task(
    key: str, *, dependencies: list[str] | None = None, status: str = "draft", **updates: object
) -> dict[str, object]:
    value: dict[str, object] = {
        "task_key": key,
        "title": f"Task {key}",
        "objective": f"Complete {key} deterministically.",
        "task_type": "implementation",
        "status": status,
        "dependency_keys": dependencies or [],
        "provenance": "human_authored",
    }
    value.update(updates)
    return value


def plan(tasks: list[dict[str, object]], **updates: object) -> PlanInput:
    value: dict[str, object] = {
        "stable_plan_key": "test-plan",
        "title": "Test strict planning",
        "objective": "Validate a deterministic task graph.",
        "planner_provenance": "human_authored",
        "repository_ref": "test-repository",
        "tasks": tasks,
    }
    value.update(updates)
    return PlanInput.model_validate(value)


def service(tmp_path: Path, now: datetime = NOW) -> PlanningService:
    return PlanningService(PlanStore(tmp_path / "plans"), clock=lambda: now)


def test_minimal_complete_schema_identity_and_timestamp_independence(tmp_path: Path) -> None:
    complete = task(
        "one",
        status="ready",
        description="Detailed work",
        acceptance_criteria=[
            {
                "criterion_id": "tests-pass",
                "description": "All tests pass.",
                "verification_method": "Run the complete test suite.",
                "required_evidence_type": "test_result",
                "mandatory": True,
                "status": "pending",
                "provenance": "human_authored",
            }
        ],
        required_evidence=[
            {
                "evidence_id": "test-output",
                "evidence_type": "test_result",
                "description": "Complete test output.",
                "mandatory": True,
            }
        ],
        declared_inputs=[{"name": "request", "description": "User request"}],
        expected_outputs=[{"name": "report", "description": "Validation report"}],
        affected_scopes=["src/**"],
        forbidden_scopes=["secrets/**"],
        context_requirements={"required_files": ["src/app.py"], "expected_context_class": "small"},
        complexity="small",
        context_class="small",
        estimated_context_tokens=100,
        requested_capabilities=["read_repository", "run_tests"],
        risk_flags=["public-api"],
    )
    first, report = service(tmp_path / "one", NOW).validate(plan([complete]))
    second, _ = service(tmp_path / "two", NOW + timedelta(days=1)).validate(plan([complete]))
    assert report.valid and first.plan_id == second.plan_id
    assert first.graph_fingerprint == second.graph_fingerprint
    assert first.tasks[0].task_id == second.tasks[0].task_id
    assert first.tasks[0].granted_capabilities == ()
    assert report.capability_summary == {"read_repository": 1, "run_tests": 1}


def test_input_order_independent_fingerprints_and_topology(tmp_path: Path) -> None:
    values = [
        task("root", status="completed"),
        task("left", dependencies=["root"]),
        task("right", dependencies=["root"]),
        task("leaf", dependencies=["left", "right"]),
    ]
    first, first_report = service(tmp_path / "one").validate(plan(values))
    second, second_report = service(tmp_path / "two").validate(plan(list(reversed(values))))
    assert first.graph_fingerprint == second.graph_fingerprint
    assert first_report.topological_task_ids == second_report.topological_task_ids
    assert first_report.root_count == 1 and first_report.leaf_count == 1
    assert first_report.maximum_dependency_depth == 2


def test_changed_objective_and_dependency_change_fingerprints(tmp_path: Path) -> None:
    first, _ = service(tmp_path / "one").validate(plan([task("a"), task("b")]))
    objective, _ = service(tmp_path / "two").validate(plan([task("a", objective="Different objective"), task("b")]))
    dependency, _ = service(tmp_path / "three").validate(plan([task("a"), task("b", dependencies=["a"])]))
    assert first.graph_fingerprint != objective.graph_fingerprint
    assert first.graph_fingerprint != dependency.graph_fingerprint


@pytest.mark.parametrize(
    "tasks",
    [
        [task("a", dependencies=["missing"])],
        [task("a", dependencies=["a"])],
        [task("a", dependencies=["b"]), task("b", dependencies=["a"])],
        [task("a", dependencies=["b"]), task("b", dependencies=["c"]), task("c", dependencies=["a"])],
    ],
)
def test_missing_self_and_dependency_cycles_are_detailed(tmp_path: Path, tasks: list[dict[str, object]]) -> None:
    _, report = service(tmp_path).validate(plan(tasks))
    assert not report.valid
    assert report.errors or report.detected_cycles
    if report.detected_cycles:
        cycle = report.detected_cycles[0]
        assert cycle.canonical_path[0] == min(cycle.involved_task_ids)
        assert cycle.canonical_path[-1] == cycle.canonical_path[0]


def test_multiple_cycles_parent_cycle_soft_cycle_and_disconnected_components(tmp_path: Path) -> None:
    values = [
        task("a", dependencies=["b"]),
        task("b", dependencies=["a"]),
        task("c", dependencies=["d"]),
        task("d", dependencies=["c"]),
        task("e", parent_task_key="f"),
        task("f", parent_task_key="e"),
        task("g", soft_dependency_keys=["h"]),
        task("h", soft_dependency_keys=["g"]),
        task("isolated"),
    ]
    _, report = service(tmp_path).validate(plan(values))
    assert [item.code for item in report.detected_cycles] == [
        "dependency_cycle",
        "dependency_cycle",
        "parent_cycle",
        "soft_dependency_cycle",
    ]


def test_duplicate_edges_keys_criteria_metadata_capabilities_and_scopes_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate hard dependency"):
        plan([task("a"), task("b", dependencies=["a", "a"])])
    with pytest.raises(ValidationError, match="duplicate criterion"):
        plan(
            [
                task(
                    "a",
                    acceptance_criteria=[
                        {
                            "criterion_id": "x",
                            "description": "one",
                            "verification_method": "review",
                            "required_evidence_type": "human_approval",
                            "provenance": "human_authored",
                        },
                        {
                            "criterion_id": "x",
                            "description": "two",
                            "verification_method": "review",
                            "required_evidence_type": "human_approval",
                            "provenance": "human_authored",
                        },
                    ],
                )
            ]
        )
    with pytest.raises(ValidationError):
        plan([task("a", requested_capabilities=["unknown"])])
    with pytest.raises(ValidationError, match="traverses"):
        plan([task("a", affected_scopes=["../secret"])])
    with pytest.raises(ValidationError, match="executable-looking"):
        plan([task("a", metadata={"shell_command": "do something"})])


def test_status_readiness_completion_consistency_and_transitions(tmp_path: Path) -> None:
    valid = plan(
        [
            task("done", status="completed"),
            task("ready", dependencies=["done"], status="ready"),
            task("blocked", status="blocked"),
            task("draft"),
        ]
    )
    _, report = service(tmp_path).validate(valid)
    ids = {item.task_key: item.task_id for item in service(tmp_path).validate(valid)[0].tasks}
    assert report.valid
    assert report.readiness is not None
    assert report.readiness.ready_task_ids == (ids["ready"],)
    assert ids["blocked"] in report.readiness.explicitly_blocked_task_ids
    invalid = plan([task("root"), task("ready", dependencies=["root"], status="ready")])
    assert not service(tmp_path / "invalid").validate(invalid)[1].valid
    validate_transition(TaskStatus.DRAFT, TaskStatus.READY)
    with pytest.raises(PlanTransitionError):
        validate_transition(TaskStatus.DRAFT, TaskStatus.COMPLETED)


def test_revision_persistence_idempotence_history_and_transition(tmp_path: Path) -> None:
    planning = service(tmp_path)
    original = plan([task("one")])
    first, _, persisted = planning.import_plan(original)
    assert persisted and first.current_revision == 1
    again, _, persisted = planning.import_plan(original)
    assert not persisted and again.revision_fingerprint == first.revision_fingerprint
    changed = original.model_copy(update={"tasks": (original.tasks[0].model_copy(update={"description": "changed"}),)})
    second, _, persisted = planning.import_plan(changed, revision_reason="Describe work")
    assert persisted and second.current_revision == 2
    assert second.previous_revision_fingerprint == first.revision_fingerprint
    assert planning.store.load_revision(first.plan_id, 1) == first
    assert [item.current_revision for item in planning.store.history(first.plan_id)] == [1, 2]
    wrong = second.model_copy(
        update={
            "current_revision": 3,
            "previous_revision_fingerprint": "0" * 64,
            "revision_fingerprint": "0" * 64,
        }
    )
    wrong = wrong.model_copy(update={"revision_fingerprint": revision_fingerprint(wrong)})
    with pytest.raises(PlanPersistenceError, match="exact current revision fingerprint"):
        planning.store.save_revision(wrong)
    third = planning.transition_task(
        first.plan_id,
        second.tasks[0].task_id,
        TaskStatus.READY,
        reason="Human marked ready",
        author=PlannerProvenance.HUMAN_AUTHORED,
    )
    assert third.current_revision == 3 and third.tasks[0].status == TaskStatus.READY


def test_invalid_revision_never_persists_and_safe_store_errors(tmp_path: Path) -> None:
    planning = service(tmp_path)
    first, _, _ = planning.import_plan(plan([task("one")]))
    invalid = plan([task("a", dependencies=["b"]), task("b", dependencies=["a"])], plan_id=first.plan_id)
    with pytest.raises(PlanValidationError):
        planning.import_plan(invalid)
    assert len(planning.store.history(first.plan_id)) == 1
    with pytest.raises(PlanRevisionNotFoundError):
        planning.store.load_revision(first.plan_id, 99)
    with pytest.raises(PlanNotFoundError):
        planning.store.load_current("../escape")
    with pytest.raises(PlanPersistenceError):
        planning.store.save_revision(first)


def test_deterministic_serialization_malformed_and_unsupported_schema(tmp_path: Path) -> None:
    planning = service(tmp_path)
    revision, _, _ = planning.import_plan(plan([task("one")]))
    assert planning.store.serialize_revision(revision) == planning.store.serialize_revision(revision)
    path = planning.store.directory / revision.plan_id / "revisions" / "000001.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(PlanFormatError, match="malformed"):
        planning.store.load_revision(revision.plan_id, 1)
    path.write_bytes(orjson.dumps({"schema_version": 99}))
    with pytest.raises(PlanFormatError, match="Unsupported"):
        planning.store.load_revision(revision.plan_id, 1)


def test_strict_json_duplicate_keys_file_size_and_planner_envelope(tmp_path: Path) -> None:
    with pytest.raises(PlanFormatError, match="Duplicate JSON"):
        PlanningService.parse_json(b'{"stable_plan_key":"a","stable_plan_key":"b"}')
    with pytest.raises(PlanFormatError, match="nesting exceeds"):
        PlanningService.parse_json(("[" * 2_000 + "]" * 2_000).encode())
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    with pytest.raises(PlanFormatError, match="safe limit"):
        service(tmp_path).parse_file(path)
    envelope = PlannerOutputEnvelope(
        planning_request_fingerprint="a" * 64,
        plan=plan([task("one")]),
        planner_provenance=PlannerProvenance.HUMAN_AUTHORED,
        confidence="unknown",
    )
    assert envelope.plan.tasks[0].status == TaskStatus.DRAFT


def test_empty_invalid_enum_overlong_and_excessive_metadata_are_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one task"):
        plan([])
    with pytest.raises(ValidationError):
        plan([task("one", task_type="execute-now")])
    with pytest.raises(ValidationError):
        plan([task("one", title="x" * 501)])
    with pytest.raises(ValidationError):
        plan([task("one", metadata={f"key-{index}": index for index in range(65)})])
    with pytest.raises(ValidationError, match="required_evidence_type"):
        plan(
            [
                task(
                    "one",
                    acceptance_criteria=[
                        {
                            "criterion_id": "proof",
                            "description": "Evidence is required.",
                            "verification_method": "Inspect evidence.",
                            "provenance": "human_authored",
                        }
                    ],
                )
            ]
        )


def test_atomic_writes_pointer_mismatch_and_fingerprint_tampering_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planning = service(tmp_path)
    import infinitecontex.planning.store as store_module

    links: list[tuple[Path, Path]] = []
    replacements: list[tuple[Path, Path]] = []
    real_link = store_module.os.link
    real_replace = store_module.os.replace

    def observed_link(source: str | Path, target: str | Path) -> None:
        links.append((Path(source), Path(target)))
        real_link(source, target)

    def observed_replace(source: str | Path, target: str | Path) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(store_module.os, "link", observed_link)
    monkeypatch.setattr(store_module.os, "replace", observed_replace)
    revision, _, _ = planning.import_plan(plan([task("one")]))
    assert links[0][0].parent == links[0][1].parent
    assert replacements[0][0].parent == replacements[0][1].parent

    revision_path = planning.store.directory / revision.plan_id / "revisions" / "000001.json"
    payload = orjson.loads(revision_path.read_bytes())
    payload["tasks"][0]["objective"] = "tampered"
    revision_path.write_bytes(orjson.dumps(payload))
    with pytest.raises(PlanFormatError, match="malformed"):
        planning.store.load_revision(revision.plan_id, 1)


def test_deterministic_ten_thousand_task_validation(tmp_path: Path) -> None:
    values = [task(f"t-{index:05d}") for index in range(10_000)]
    first, first_report = service(tmp_path / "one").validate(plan(values))
    second, second_report = service(tmp_path / "two").validate(plan(list(reversed(values))))
    assert first_report.valid and first_report.task_count == 10_000
    assert first_report.edge_count == 0 and first_report.root_count == 10_000 and first_report.leaf_count == 10_000
    assert first.graph_fingerprint == second.graph_fingerprint
    assert first_report.topological_task_ids == second_report.topological_task_ids
