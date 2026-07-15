from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import orjson
import pytest
from pydantic import ValidationError

from infinitecontex.planning.models import Capability, PlanInput
from infinitecontex.planning.service import PlanningService
from infinitecontex.planning.store import PlanStore
from infinitecontex.task_context.scopes import normalize_repository_scope
from infinitecontex.tools.builtins import builtin_registry, make_tool_definition
from infinitecontex.tools.capabilities import capability_from_name, capability_gaps
from infinitecontex.tools.errors import (
    ContradictoryEffectsError,
    DuplicateToolIdentityError,
    MalformedToolDecisionError,
    MalformedToolDefinitionError,
    UnknownCapabilityError,
    UnsupportedRegistrySchemaError,
)
from infinitecontex.tools.fingerprints import definition_fingerprint
from infinitecontex.tools.models import (
    ApprovalClass,
    FieldType,
    ImplementationStatus,
    PathFieldSemantics,
    PolicyDecision,
    RiskLevel,
    SchemaField,
    ScopeKind,
    ToolCategory,
    ToolEffects,
    ToolSchema,
    ToolScope,
)
from infinitecontex.tools.policy import ToolPolicy
from infinitecontex.tools.registry import ToolRegistry
from infinitecontex.tools.risk import derive_minimum_risk
from infinitecontex.tools.scopes import evaluate_tool_scopes
from infinitecontex.tools.store import ToolDecisionStore
from infinitecontex.tools.validation import validate_tool_definition

NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


def _plan(tmp_path: Path, *, capabilities: tuple[str, ...] = ()):
    value = PlanInput.model_validate(
        {
            "stable_plan_key": "tool-policy-plan",
            "title": "Inspect tool policy",
            "objective": "Evaluate one task and tool deterministically.",
            "status": "active",
            "planner_provenance": "human_authored",
            "repository_ref": "test-repository",
            "tasks": [
                {
                    "task_key": "inspect",
                    "title": "Inspect tool",
                    "objective": "Inspect without execution.",
                    "task_type": "analysis",
                    "status": "ready",
                    "affected_scopes": ["src/infinitecontex/tools"],
                    "forbidden_scopes": ["secrets/**"],
                    "requested_capabilities": list(capabilities),
                    "provenance": "human_authored",
                }
            ],
        }
    )
    return PlanningService(PlanStore(tmp_path / "plans"), clock=lambda: NOW).validate(value)[0]


def _refingerprint(definition, **updates: object):
    provisional = definition.model_copy(update={**updates, "definition_fingerprint": "0" * 64})
    return provisional.model_copy(update={"definition_fingerprint": definition_fingerprint(provisional)})


def test_strict_schema_minimal_complete_defaults_and_duplicate_fields() -> None:
    minimal = make_tool_definition(
        "sample.minimal",
        display_name="Minimal sample",
        category=ToolCategory.OTHER,
        capabilities=(),
        effects=ToolEffects(read_only=True),
    )
    assert validate_tool_definition(minimal) == minimal
    complete = make_tool_definition(
        "sample.complete",
        display_name="Complete sample",
        category=ToolCategory.FILE_READ,
        capabilities=(Capability.READ_REPOSITORY,),
        effects=ToolEffects(read_only=True, reads_repository=True),
        scopes=(ToolScope(kind=ScopeKind.EXPLICIT_PATH_READ, input_field="path"),),
        inputs=(
            SchemaField(
                name="path",
                field_type=FieldType.STRING,
                required=False,
                description="Repository path",
                maximum_length=100,
                default="README.md",
                path_semantics=PathFieldSemantics.REPOSITORY_RELATIVE,
            ),
        ),
    )
    assert complete.input_schema.fields[0].default == "README.md"
    with pytest.raises(ValidationError, match="duplicate field"):
        ToolSchema(fields=(complete.input_schema.fields[0], complete.input_schema.fields[0]))
    with pytest.raises(ValidationError):
        SchemaField(name="bad", field_type="opaque", description="bad")
    with pytest.raises(ValidationError, match="default"):
        SchemaField(name="count", field_type=FieldType.INTEGER, required=False, description="count", default="x")


def test_definition_identity_order_effect_conflicts_risk_floor_and_handler_rejection() -> None:
    first = make_tool_definition(
        "sample.identity",
        display_name="Identity sample",
        category=ToolCategory.OTHER,
        capabilities=(Capability.RUN_TESTS, Capability.READ_REPOSITORY),
        effects=ToolEffects(runs_local_processes=True),
    )
    second = make_tool_definition(
        "sample.identity",
        display_name="Identity sample",
        category=ToolCategory.OTHER,
        capabilities=(Capability.READ_REPOSITORY, Capability.RUN_TESTS),
        effects=ToolEffects(runs_local_processes=True),
    )
    assert first.tool_id == second.tool_id and first.definition_fingerprint == second.definition_fingerprint
    conflict = _refingerprint(
        first,
        effects=ToolEffects(read_only=True, writes_repository_files=True),
        scopes=(ToolScope(kind=ScopeKind.AFFECTED_SCOPE_WRITE),),
        declared_risk=RiskLevel.MEDIUM,
        derived_risk=RiskLevel.MEDIUM,
        approval_class=ApprovalClass.PER_TASK_APPROVAL_REQUIRED,
    )
    with pytest.raises(ContradictoryEffectsError):
        validate_tool_definition(conflict)
    understated = _refingerprint(first, declared_risk=RiskLevel.LOW)
    with pytest.raises(MalformedToolDefinitionError, match="understates"):
        validate_tool_definition(understated)
    handler = _refingerprint(first, execution_handler="package:function")
    with pytest.raises(MalformedToolDefinitionError, match="handlers"):
        validate_tool_definition(handler)
    assert derive_minimum_risk(ToolEffects(read_only=True)) == RiskLevel.LOW
    assert derive_minimum_risk(ToolEffects(writes_repository_files=True)) == RiskLevel.MEDIUM
    assert derive_minimum_risk(ToolEffects(runs_local_processes=True)) == RiskLevel.HIGH
    assert derive_minimum_risk(ToolEffects(deletes_data=True)) == RiskLevel.CRITICAL


def test_registry_load_lookup_filter_duplicate_export_reverse_and_schema() -> None:
    definitions = builtin_registry().list()
    forward = ToolRegistry(definitions)
    reverse = ToolRegistry(reversed(definitions))
    assert forward.fingerprint == reverse.fingerprint
    assert forward.export_json() == reverse.export_json()
    assert [item.canonical_name for item in forward.list()] == sorted(item.canonical_name for item in definitions)
    sample = forward.get_by_name_version("repository.read-file", "1.0.0")
    assert forward.get(sample.tool_id) == sample
    assert sample in forward.list(category=ToolCategory.FILE_READ)
    assert sample in forward.list(capability=Capability.READ_REPOSITORY)
    assert sample in forward.list(implementation_status=ImplementationStatus.DECLARED_ONLY)
    with pytest.raises(DuplicateToolIdentityError):
        forward.register(sample)
    with pytest.raises(UnsupportedRegistrySchemaError):
        ToolRegistry(schema_version=99)
    assert not hasattr(forward, "register_provider")
    assert b"execution_handler" in forward.export_json() and b'"execution_handler": null' in forward.export_json()


def test_capability_mapping_requests_are_never_grants() -> None:
    assert capability_from_name("read_repository") == Capability.READ_REPOSITORY
    with pytest.raises(UnknownCapabilityError):
        capability_from_name("superuser")
    missing_requested, missing_granted = capability_gaps(
        (Capability.READ_REPOSITORY, Capability.RUN_TESTS),
        (Capability.READ_REPOSITORY,),
    )
    assert missing_requested == ()
    assert missing_granted == (Capability.READ_REPOSITORY,)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("src\\app.py", "src/app.py"),
        ("./docs/guide.md", "docs/guide.md"),
        ("../secret", None),
        ("C:/outside", None),
        ("//server/share", None),
    ],
)
def test_scope_normalization_windows_posix_and_escape(value: str, expected: str | None) -> None:
    normalized, error = normalize_repository_scope(value)
    assert normalized == expected
    assert (error is None) == (expected is not None)


def test_scope_read_write_test_docs_and_forbidden_overlap(tmp_path: Path) -> None:
    task = _plan(tmp_path).tasks[0]
    path_field = SchemaField(
        name="path",
        field_type=FieldType.STRING,
        description="path",
        path_semantics=PathFieldSemantics.AFFECTED_SCOPE,
    )
    write = make_tool_definition(
        "sample.scope-write",
        display_name="Scope write",
        category=ToolCategory.FILE_WRITE,
        capabilities=(),
        effects=ToolEffects(writes_repository_files=True),
        scopes=(ToolScope(kind=ScopeKind.AFFECTED_SCOPE_WRITE, input_field="path"),),
        inputs=(path_field,),
    )
    assert evaluate_tool_scopes(task, write, None)[0].passed
    tests = _refingerprint(
        write,
        scopes=(ToolScope(kind=ScopeKind.TEST_ONLY_WRITE, input_field="path"),),
    )
    assert not evaluate_tool_scopes(task, tests, None)[0].passed
    docs = _refingerprint(
        write,
        scopes=(ToolScope(kind=ScopeKind.DOCUMENTATION_ONLY_WRITE, input_field="path"),),
    )
    assert not evaluate_tool_scopes(task, docs, None)[0].passed
    overlapping = task.model_copy(update={"affected_scopes": ("secrets/file.txt",)})
    assert evaluate_tool_scopes(overlapping, write, None)[0].reason_code == "forbidden_scope_overlap"


def test_policy_read_only_missing_capability_mutation_and_forbidden(tmp_path: Path) -> None:
    registry = builtin_registry()
    plan = _plan(tmp_path, capabilities=("run_tests",))
    task = plan.tasks[0]
    policy = ToolPolicy(clock=lambda: NOW)
    inspect = policy.evaluate(
        plan,
        task,
        registry.get_by_name_version("plan.inspect", "1.0.0"),
        registry_fingerprint=registry.fingerprint,
    )
    assert inspect.decision == PolicyDecision.ELIGIBLE_FOR_FUTURE_REQUEST
    assert inspect.structurally_eligible and not inspect.executable_now and inspect.granted_capabilities == ()
    missing = policy.evaluate(
        plan,
        task,
        registry.get_by_name_version("execution.run-build", "1.0.0"),
        registry_fingerprint=registry.fingerprint,
        plan_approved=True,
    )
    assert missing.decision == PolicyDecision.DENIED_CAPABILITY_NOT_REQUESTED
    future = policy.evaluate(
        plan,
        task,
        registry.get_by_name_version("execution.run-tests", "1.0.0"),
        registry_fingerprint=registry.fingerprint,
        plan_approved=True,
    )
    assert future.decision == PolicyDecision.ELIGIBLE_WITH_HUMAN_APPROVAL
    assert future.missing_granted_capabilities == (Capability.RUN_TESTS,)
    available = make_tool_definition(
        "sample.future-execution",
        display_name="Future execution",
        category=ToolCategory.TEST_EXECUTION,
        capabilities=(Capability.RUN_TESTS,),
        effects=ToolEffects(runs_local_processes=True),
        status=ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION,
    )
    not_granted = policy.evaluate(
        plan,
        task,
        available,
        registry_fingerprint=registry.fingerprint,
        plan_approved=True,
    )
    assert not_granted.decision == PolicyDecision.DENIED_CAPABILITY_NOT_GRANTED
    forbidden = policy.evaluate(
        plan,
        task,
        registry.get_by_name_version("git.force-push", "1.0.0"),
        registry_fingerprint=registry.fingerprint,
    )
    assert forbidden.decision == PolicyDecision.PERMANENTLY_FORBIDDEN
    assert forbidden.approval_class == ApprovalClass.PERMANENTLY_FORBIDDEN
    assert (
        inspect.semantic_fingerprint
        == policy.evaluate(
            plan,
            task,
            registry.get_by_name_version("plan.inspect", "1.0.0"),
            registry_fingerprint=registry.fingerprint,
        ).semantic_fingerprint
    )


def test_decision_persistence_is_immutable_deterministic_and_malformed_fails(tmp_path: Path) -> None:
    registry = builtin_registry()
    plan = _plan(tmp_path)
    decision = ToolPolicy(clock=lambda: NOW).evaluate(
        plan,
        plan.tasks[0],
        registry.get_by_name_version("plan.inspect", "1.0.0"),
        registry_fingerprint=registry.fingerprint,
    )
    store = ToolDecisionStore(tmp_path / "plans")
    path = store.save(decision)
    assert store.save(decision) == path
    assert store.load(plan.plan_id, decision.decision_id) == decision
    assert store.list(plan.plan_id, task_id=plan.tasks[0].task_id) == (decision,)
    assert store.serialize(decision) == store.serialize(decision)
    payload = orjson.loads(path.read_bytes())
    assert "source" not in payload and "secret" not in payload
    path.write_bytes(orjson.dumps({"schema_version": 99}))
    with pytest.raises(MalformedToolDecisionError, match="Unsupported"):
        store.load(plan.plan_id, decision.decision_id)


def test_scale_one_thousand_registry_and_policy_decisions(tmp_path: Path) -> None:
    definitions = tuple(
        make_tool_definition(
            f"scale.tool-{index:04d}",
            display_name=f"Scale tool {index}",
            category=ToolCategory.OTHER,
            capabilities=(),
            effects=ToolEffects(read_only=True),
        )
        for index in range(1000)
    )
    forward = ToolRegistry(definitions)
    reverse = ToolRegistry(reversed(definitions))
    assert len(forward.list()) == 1000 and forward.fingerprint == reverse.fingerprint
    plan = _plan(tmp_path)
    policy = ToolPolicy(clock=lambda: NOW)
    decisions = tuple(
        policy.evaluate(plan, plan.tasks[0], item, registry_fingerprint=forward.fingerprint) for item in forward.list()
    )
    assert len(decisions) == 1000 and len({item.decision_id for item in decisions}) == 1000
    assert all(not item.executable_now for item in decisions)
