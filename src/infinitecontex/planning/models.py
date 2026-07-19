"""Strict immutable contracts for plans, tasks, validation, and revisions."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

PLAN_SCHEMA_VERSION = 1
PLANNING_POLICY_ID = "strict-task-dag-v1"
MAX_TASKS = 10_000
MAX_PLAN_FILE_BYTES = 8 * 1024 * 1024
MAX_DEPENDENCIES = 64
MAX_CRITERIA = 1_000
MAX_EVIDENCE = 64
MAX_CONTEXT_REFERENCES = 256
MAX_METADATA_KEYS = 64

Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]
PlanId = Annotated[str, StringConstraints(pattern=r"^plan-[0-9a-f]{24}$")]
TaskId = Annotated[str, StringConstraints(pattern=r"^task-[0-9a-f]{24}$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16_000)]
ScopeText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlannerProvenance(StrEnum):
    HUMAN_AUTHORED = "human_authored"
    IMPORTED = "imported"
    DETERMINISTIC_TRANSFORMATION = "deterministic_transformation"
    FUTURE_LLM_AUTHORED = "future_llm_authored"
    UNKNOWN_LEGACY = "unknown_legacy"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class TaskType(StrEnum):
    ANALYSIS = "analysis"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    TEST = "test"
    DOCUMENTATION = "documentation"
    REVIEW = "review"
    INTEGRATION = "integration"
    MIGRATION = "migration"
    INVESTIGATION = "investigation"
    VALIDATION = "validation"
    RELEASE = "release"
    MANUAL_APPROVAL = "manual_approval"
    OTHER = "other"


class TaskStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    BLOCKED = "blocked"
    IN_PROGRESS = "in_progress"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class TaskPriority(IntEnum):
    LOW = 25
    NORMAL = 50
    HIGH = 75
    CRITICAL = 100


class CriterionStatus(StrEnum):
    PENDING = "pending"
    SATISFIED = "satisfied"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"
    WAIVED = "waived"
    SUPERSEDED = "superseded"


class EvidenceType(StrEnum):
    TEST_RESULT = "test_result"
    STATIC_ANALYSIS_RESULT = "static_analysis_result"
    BUILD_RESULT = "build_result"
    FILE_CHANGE = "file_change"
    HUMAN_APPROVAL = "human_approval"
    GENERATED_ARTIFACT = "generated_artifact"
    RUNTIME_OBSERVATION = "runtime_observation"
    DOCUMENTATION_UPDATE = "documentation_update"
    EXTERNAL_VERIFICATION = "external_verification"
    OTHER = "other"


class Capability(StrEnum):
    READ_REPOSITORY = "read_repository"
    INSPECT_GIT = "inspect_git"
    RUN_TESTS = "run_tests"
    RUN_BUILD = "run_build"
    WRITE_SOURCE_FILES = "write_source_files"
    WRITE_TESTS = "write_tests"
    WRITE_DOCUMENTATION = "write_documentation"
    WRITE_ARTIFACTS = "write_artifacts"
    EXECUTE_COMMANDS = "execute_commands"
    USE_NETWORK = "use_network"
    MUTATE_PLANS = "mutate_plans"
    MUTATE_TASK_STATUS = "mutate_task_status"
    REQUEST_HUMAN_APPROVAL = "request_human_approval"
    CREATE_COMMITS = "create_commits"
    PUSH_COMMITS = "push_commits"


class ComplexityClass(StrEnum):
    TRIVIAL = "trivial"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    UNKNOWN = "unknown"


class ContextClass(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    OVERSIZED = "oversized"
    UNKNOWN = "unknown"


class ConfidenceClass(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class AcceptanceCriterion(StrictModel):
    criterion_id: Identifier
    description: LongText
    verification_method: ShortText
    required_evidence_type: EvidenceType
    mandatory: bool = True
    status: CriterionStatus = CriterionStatus.PENDING
    provenance: PlannerProvenance


class EvidenceRequirement(StrictModel):
    evidence_id: Identifier
    evidence_type: EvidenceType
    description: ShortText
    mandatory: bool = True


class DeclaredData(StrictModel):
    name: Identifier
    description: ShortText
    reference: str | None = Field(default=None, max_length=1000)


class ContextPathDeclaration(StrictModel):
    value: ScopeText
    kind: Literal[
        "exact_file",
        "exact_directory",
        "glob",
        "test_file",
        "documentation_file",
        "configuration_file",
        "source_range",
        "logical_scope",
    ] = "exact_file"
    requirement: Literal["required", "optional", "forbidden"] = "required"
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    expected_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_repository_snapshot: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    language_hint: str | None = Field(default=None, max_length=100)
    logical_label: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_path_declaration(self) -> "ContextPathDeclaration":
        _validate_scope(self.value)
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("source ranges require both line_start and line_end")
        if self.line_start is not None and self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end cannot precede line_start")
        if self.kind == "source_range" and self.line_start is None:
            raise ValueError("source_range declarations require a line range")
        if self.kind not in {"glob", "logical_scope"} and any(value in self.value for value in "*?["):
            raise ValueError("wildcards require a glob or logical_scope declaration")
        return self


class ContextSymbolDeclaration(StrictModel):
    reference: ShortText
    language: ShortText
    symbol_name: ShortText
    qualified_name: str | None = Field(default=None, max_length=1000)
    file_hint: ScopeText | None = None
    module_or_namespace: str | None = Field(default=None, max_length=1000)
    symbol_kind: Literal[
        "module",
        "namespace",
        "class",
        "interface",
        "enum",
        "function",
        "method",
        "property",
        "field",
        "constant",
        "test",
        "unknown",
    ] = "unknown"
    signature_hint: str | None = Field(default=None, max_length=1000)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    requirement: Literal["required", "optional", "forbidden"] = "required"
    expected_symbol_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_symbol_declaration(self) -> "ContextSymbolDeclaration":
        if self.file_hint is not None:
            _validate_scope(self.file_hint)
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("symbol ranges require both line_start and line_end")
        if self.line_start is not None and self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end cannot precede line_start")
        return self


class ContextRequirements(StrictModel):
    required_files: tuple[ScopeText, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    required_symbols: tuple[ShortText, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    required_decisions: tuple[Identifier, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    required_task_outputs: tuple[Identifier, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    required_tests: tuple[ShortText, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    required_diagnostics: tuple[ShortText, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    required_documentation: tuple[ScopeText, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    required_conversation_refs: tuple[Identifier, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    optional_files: tuple[ScopeText, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    optional_symbols: tuple[ShortText, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    optional_tests: tuple[ShortText, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    optional_documentation: tuple[ScopeText, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    optional_decisions: tuple[Identifier, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    optional_task_outputs: tuple[Identifier, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    path_references: tuple[ContextPathDeclaration, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    symbol_references: tuple[ContextSymbolDeclaration, ...] = Field(default=(), max_length=MAX_CONTEXT_REFERENCES)
    expected_context_class: ContextClass = ContextClass.UNKNOWN
    maximum_context_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_total_references(self) -> "ContextRequirements":
        total = sum(
            len(value)
            for name, value in self
            if name.startswith(("required_", "optional_")) or name in {"path_references", "symbol_references"}
        )
        if total > MAX_CONTEXT_REFERENCES:
            raise ValueError(f"context references exceed the limit of {MAX_CONTEXT_REFERENCES}")
        return self


class RetryPolicy(StrictModel):
    maximum_future_attempts: int = Field(default=0, ge=0, le=10)
    retry_requires_approval: bool = True


class TaskInput(StrictModel):
    task_id: TaskId | None = None
    task_key: Identifier
    title: ShortText
    objective: LongText
    description: str = Field(default="", max_length=16_000)
    task_type: TaskType
    status: TaskStatus = TaskStatus.DRAFT
    priority: TaskPriority = TaskPriority.NORMAL
    dependency_keys: tuple[Identifier, ...] = Field(default=(), max_length=MAX_DEPENDENCIES)
    soft_dependency_keys: tuple[Identifier, ...] = Field(default=(), max_length=MAX_DEPENDENCIES)
    parent_task_key: Identifier | None = None
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = Field(default=(), max_length=MAX_CRITERIA)
    required_evidence: tuple[EvidenceRequirement, ...] = Field(default=(), max_length=MAX_EVIDENCE)
    declared_inputs: tuple[DeclaredData, ...] = Field(default=(), max_length=128)
    expected_outputs: tuple[DeclaredData, ...] = Field(default=(), max_length=128)
    affected_scopes: tuple[ScopeText, ...] = Field(default=(), max_length=128)
    forbidden_scopes: tuple[ScopeText, ...] = Field(default=(), max_length=128)
    context_requirements: ContextRequirements = Field(default_factory=ContextRequirements)
    complexity: ComplexityClass = ComplexityClass.UNKNOWN
    context_class: ContextClass = ContextClass.UNKNOWN
    estimated_context_tokens: int | None = Field(default=None, ge=0)
    requested_capabilities: tuple[Capability, ...] = Field(default=(), max_length=32)
    granted_capabilities: tuple[Capability, ...] = Field(default=(), max_length=0)
    risk_flags: tuple[Identifier, ...] = Field(default=(), max_length=64)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    provenance: PlannerProvenance
    metadata: dict[str, str | int | bool | None] = Field(default_factory=dict, max_length=MAX_METADATA_KEYS)

    @model_validator(mode="after")
    def validate_task_collections(self) -> "TaskInput":
        _unique(self.dependency_keys, "hard dependency")
        _unique(self.soft_dependency_keys, "soft dependency")
        _unique((item.criterion_id for item in self.acceptance_criteria), "criterion ID")
        _unique((item.evidence_id for item in self.required_evidence), "evidence ID")
        _unique(self.requested_capabilities, "requested capability")
        _validate_metadata(self.metadata)
        for scope in (*self.affected_scopes, *self.forbidden_scopes, *self.context_requirements.required_files):
            _validate_scope(scope)
        if set(self.affected_scopes) & set(self.forbidden_scopes):
            raise ValueError("affected and forbidden scopes cannot contain the same value")
        return self


class PlanInput(StrictModel):
    schema_version: Literal[1] = 1
    plan_id: PlanId | None = None
    stable_plan_key: Identifier
    title: ShortText
    objective: LongText
    status: PlanStatus = PlanStatus.DRAFT
    planner_provenance: PlannerProvenance
    parent_plan_id: PlanId | None = None
    originating_request_ref: str | None = Field(default=None, max_length=1000)
    repository_ref: ShortText
    target_branch: str | None = Field(default=None, max_length=500)
    model_profile_ref: Identifier | None = None
    planning_policy_version: Literal[1] = 1
    warnings: tuple[ShortText, ...] = Field(default=(), max_length=64)
    metadata: dict[str, str | int | bool | None] = Field(default_factory=dict, max_length=MAX_METADATA_KEYS)
    tasks: tuple[TaskInput, ...] = Field(max_length=MAX_TASKS)

    @field_validator("tasks")
    @classmethod
    def require_tasks(cls, value: tuple[TaskInput, ...]) -> tuple[TaskInput, ...]:
        if not value:
            raise ValueError("a plan requires at least one task")
        return value

    @model_validator(mode="after")
    def validate_plan_metadata(self) -> "PlanInput":
        _validate_metadata(self.metadata)
        return self


class Task(StrictModel):
    task_id: TaskId
    plan_id: PlanId
    task_key: Identifier
    title: ShortText
    objective: LongText
    description: str = Field(max_length=16_000)
    task_type: TaskType
    status: TaskStatus
    priority: TaskPriority
    dependency_ids: tuple[TaskId, ...]
    soft_dependency_ids: tuple[TaskId, ...]
    parent_task_id: TaskId | None
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    required_evidence: tuple[EvidenceRequirement, ...]
    declared_inputs: tuple[DeclaredData, ...]
    expected_outputs: tuple[DeclaredData, ...]
    affected_scopes: tuple[ScopeText, ...]
    forbidden_scopes: tuple[ScopeText, ...]
    context_requirements: ContextRequirements
    complexity: ComplexityClass
    context_class: ContextClass
    estimated_context_tokens: int | None = Field(ge=0)
    requested_capabilities: tuple[Capability, ...]
    granted_capabilities: tuple[Capability, ...] = Field(max_length=0)
    risk_flags: tuple[Identifier, ...]
    retry_policy: RetryPolicy
    provenance: PlannerProvenance
    metadata: dict[str, str | int | bool | None]
    created_at: datetime
    updated_at: datetime
    task_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlanRevision(StrictModel):
    schema_version: Literal[1] = 1
    plan_id: PlanId
    stable_plan_key: Identifier
    title: ShortText
    objective: LongText
    normalized_objective_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: PlanStatus
    planner_provenance: PlannerProvenance
    created_at: datetime
    updated_at: datetime
    current_revision: int = Field(ge=1)
    previous_revision_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    revision_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision_reason: ShortText
    revision_author: PlannerProvenance
    changed_task_ids: tuple[Identifier, ...]
    structural_changes: tuple[ShortText, ...]
    superseded_revision: int | None = Field(default=None, ge=1)
    parent_plan_id: PlanId | None
    originating_request_ref: str | None
    repository_ref: ShortText
    target_branch: str | None
    model_profile_ref: Identifier | None
    planning_policy_version: Literal[1] = 1
    task_count: int = Field(ge=1, le=MAX_TASKS)
    graph_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    warnings: tuple[ShortText, ...]
    metadata: dict[str, str | int | bool | None]
    tasks: tuple[Task, ...]


class CycleDetail(StrictModel):
    code: Literal["dependency_cycle", "soft_dependency_cycle", "parent_cycle"]
    involved_task_ids: tuple[str, ...]
    canonical_path: tuple[str, ...]
    remediation: str


class ValidationIssue(StrictModel):
    code: str
    message: str
    task_id: str | None = None
    remediation: str


class ReadinessAnalysis(StrictModel):
    ready_task_ids: tuple[str, ...]
    dependency_blocked_task_ids: tuple[str, ...]
    explicitly_blocked_task_ids: tuple[str, ...]
    completed_prerequisite_closure: tuple[str, ...]
    roots: tuple[str, ...]
    leaves: tuple[str, ...]
    isolated: tuple[str, ...]
    downstream_dependents: dict[str, tuple[str, ...]]
    dependency_depths: dict[str, int]
    maximum_dependency_depth: int


class PlanValidationReport(StrictModel):
    valid: bool
    plan_id: str | None
    graph_fingerprint: str | None
    task_count: int
    edge_count: int
    root_count: int
    leaf_count: int
    maximum_dependency_depth: int
    ready_task_count: int
    blocked_task_count: int
    errors: tuple[ValidationIssue, ...]
    warnings: tuple[str, ...]
    normalized_changes: tuple[str, ...]
    detected_cycles: tuple[CycleDetail, ...]
    capability_summary: dict[str, int]
    scope_summary: dict[str, int]
    planning_policy_id: Literal["strict-task-dag-v1"] = "strict-task-dag-v1"
    planning_policy_version: Literal[1] = 1
    topological_task_ids: tuple[str, ...]
    readiness: ReadinessAnalysis | None


class CurrentPlanPointer(StrictModel):
    schema_version: Literal[1] = 1
    plan_id: PlanId
    current_revision: int = Field(ge=1)
    revision_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    graph_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_at: datetime


class PlannerOutputEnvelope(StrictModel):
    schema_version: Literal[1] = 1
    planning_request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan: PlanInput
    assumptions: tuple[ShortText, ...] = Field(default=(), max_length=128)
    unresolved_questions: tuple[ShortText, ...] = Field(default=(), max_length=128)
    risks: tuple[ShortText, ...] = Field(default=(), max_length=128)
    decomposition_rationale: tuple[ShortText, ...] = Field(default=(), max_length=128)
    declared_omissions: tuple[ShortText, ...] = Field(default=(), max_length=128)
    planner_provenance: PlannerProvenance
    confidence: ConfidenceClass


def _unique(values: Any, label: str) -> None:
    materialized = tuple(values)
    if len(set(materialized)) != len(materialized):
        raise ValueError(f"duplicate {label} values are not allowed")


def _validate_metadata(metadata: dict[str, str | int | bool | None]) -> None:
    for key, value in metadata.items():
        if not key or len(key) > 100 or any(ord(char) < 32 for char in key):
            raise ValueError("metadata keys must be printable and at most 100 characters")
        if isinstance(value, str) and len(value) > 1000:
            raise ValueError("metadata string values cannot exceed 1000 characters")
        if any(token in key.casefold() for token in ("command", "script", "shell", "executable")):
            raise ValueError("executable-looking metadata keys are forbidden; use typed task declarations")


def _validate_scope(scope: str) -> None:
    normalized = scope.replace("\\", "/")
    if normalized.startswith(("/", "//")) or ":/" in normalized or any(part == ".." for part in normalized.split("/")):
        raise ValueError(f"scope {scope!r} is absolute or traverses outside the repository")
    if any(ord(char) < 32 for char in scope):
        raise ValueError("scope contains control characters")
