"""Immutable versioned contracts for data-only tool definitions and decisions."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from infinitecontex.planning.models import Capability

TOOL_REGISTRY_SCHEMA_VERSION = 1
TOOL_SCHEMA_VERSION = 1
TOOL_DECISION_SCHEMA_VERSION = 1
TOOL_POLICY_ID = "deterministic-tool-policy-v1"
TOOL_POLICY_VERSION = 1
RISK_DERIVATION_VERSION = 1
MAX_SCHEMA_FIELDS = 128
MAX_SCOPE_RULES = 128

ToolId = Annotated[str, StringConstraints(pattern=r"^tool-[0-9a-f]{24}$")]
DecisionId = Annotated[str, StringConstraints(pattern=r"^tool-decision-[0-9a-f]{24}$")]
SafeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160, pattern=r"^[a-z][a-z0-9._-]*$"),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolCategory(StrEnum):
    REPOSITORY_READ = "repository-read"
    FILE_READ = "file-read"
    FILE_WRITE = "file-write"
    SOURCE_CODE_EDIT = "source-code-edit"
    TEST_EXECUTION = "test-execution"
    BUILD_EXECUTION = "build-execution"
    STATIC_ANALYSIS = "static-analysis"
    GIT_INSPECTION = "git-inspection"
    GIT_MUTATION = "git-mutation"
    SHELL_COMMAND = "shell-command"
    DOCUMENTATION_GENERATION = "documentation-generation"
    ARTIFACT_GENERATION = "artifact-generation"
    NETWORK_ACCESS = "network-access"
    HUMAN_APPROVAL = "human-approval"
    MODEL_INVOCATION = "model-invocation"
    TASK_PLAN_INSPECTION = "task-plan-inspection"
    CONTEXT_INSPECTION = "context-inspection"
    OTHER = "other"


class ImplementationStatus(StrEnum):
    DECLARED_ONLY = "declared_only"
    IMPLEMENTED_BUT_DISABLED = "implemented_but_disabled"
    AVAILABLE_FOR_FUTURE_EXECUTION = "available_for_future_execution"
    DEPRECATED = "deprecated"
    UNAVAILABLE = "unavailable"


class AvailabilityState(StrEnum):
    DEFINITION_AVAILABLE = "definition_available"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    PERMANENTLY_FORBIDDEN = "permanently_forbidden"


class FieldType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ENUM = "enum"
    ARRAY = "array"
    OBJECT = "object"


class PathFieldSemantics(StrEnum):
    NONE = "none"
    REPOSITORY_RELATIVE = "repository_relative"
    REPOSITORY_SOURCE_RANGE = "repository_source_range"
    AFFECTED_SCOPE = "affected_scope"
    TEST_PATH = "test_path"
    DOCUMENTATION_PATH = "documentation_path"
    INFCTX_PATH = "infctx_path"
    EXTERNAL_PATH = "external_path"
    NETWORK_DESTINATION = "network_destination"


class NormalizationRule(StrEnum):
    NONE = "none"
    TRIM = "trim"
    CASEFOLD = "casefold"
    REPOSITORY_PATH = "repository_path"
    SORT_UNIQUE = "sort_unique"


class SchemaField(StrictModel):
    name: SafeName
    field_type: FieldType
    required: bool = True
    description: str = Field(min_length=1, max_length=1000)
    minimum: float | None = None
    maximum: float | None = None
    maximum_length: int | None = Field(default=None, ge=1, le=1_000_000)
    maximum_items: int | None = Field(default=None, ge=1, le=100_000)
    permitted_values: tuple[str, ...] = Field(default=(), max_length=256)
    item_type: FieldType | None = None
    fields: tuple[SchemaField, ...] = Field(default=(), max_length=MAX_SCHEMA_FIELDS)
    default: str | int | float | bool | tuple[str, ...] | None = None
    path_semantics: PathFieldSemantics = PathFieldSemantics.NONE
    sensitive: bool = False
    excluded_from_logs: bool = False
    normalization: NormalizationRule = NormalizationRule.NONE

    @model_validator(mode="after")
    def validate_contract(self) -> SchemaField:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("field minimum cannot exceed maximum")
        if self.field_type == FieldType.ENUM and not self.permitted_values:
            raise ValueError("enum fields require permitted_values")
        if self.field_type != FieldType.ENUM and self.permitted_values:
            raise ValueError("permitted_values are valid only for enum fields")
        if self.field_type == FieldType.ARRAY and self.item_type is None:
            raise ValueError("array fields require item_type")
        if self.field_type != FieldType.ARRAY and (self.item_type is not None or self.maximum_items is not None):
            raise ValueError("item_type and maximum_items are valid only for arrays")
        if self.field_type == FieldType.OBJECT:
            if not self.fields:
                raise ValueError("object fields require a strict nested field schema")
        elif self.fields:
            raise ValueError("nested fields are valid only for object fields")
        if self.required and self.default is not None:
            raise ValueError("required fields cannot define defaults")
        if self.sensitive and not self.excluded_from_logs:
            raise ValueError("sensitive fields must be excluded from logs")
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("duplicate nested field definitions are not allowed")
        self._validate_default()
        return self

    def _validate_default(self) -> None:
        if self.default is None:
            return
        valid = {
            FieldType.STRING: isinstance(self.default, str),
            FieldType.INTEGER: isinstance(self.default, int) and not isinstance(self.default, bool),
            FieldType.NUMBER: isinstance(self.default, (int, float)) and not isinstance(self.default, bool),
            FieldType.BOOLEAN: isinstance(self.default, bool),
            FieldType.ENUM: isinstance(self.default, str) and self.default in self.permitted_values,
            FieldType.ARRAY: isinstance(self.default, tuple),
            FieldType.OBJECT: False,
        }[self.field_type]
        if not valid:
            raise ValueError("field default does not match its declared type")
        if (
            isinstance(self.default, str)
            and self.maximum_length is not None
            and len(self.default) > self.maximum_length
        ):
            raise ValueError("field default exceeds maximum_length")
        if (
            isinstance(self.default, tuple)
            and self.maximum_items is not None
            and len(self.default) > self.maximum_items
        ):
            raise ValueError("field default exceeds maximum_items")
        if isinstance(self.default, (int, float)) and not isinstance(self.default, bool):
            if self.minimum is not None and self.default < self.minimum:
                raise ValueError("field default is below minimum")
            if self.maximum is not None and self.default > self.maximum:
                raise ValueError("field default exceeds maximum")


class ToolSchema(StrictModel):
    schema_version: Literal[1] = 1
    fields: tuple[SchemaField, ...] = Field(default=(), max_length=MAX_SCHEMA_FIELDS)
    allow_unknown_fields: Literal[False] = False

    @field_validator("fields")
    @classmethod
    def unique_fields(cls, value: tuple[SchemaField, ...]) -> tuple[SchemaField, ...]:
        names = [field.name for field in value]
        if len(names) != len(set(names)):
            raise ValueError("duplicate field definitions are not allowed")
        return tuple(sorted(value, key=lambda field: field.name))


class ToolEffects(StrictModel):
    read_only: bool = False
    reads_repository: bool = False
    writes_repository_files: bool = False
    writes_infctx: bool = False
    modifies_git_index: bool = False
    creates_commits: bool = False
    pushes_remotely: bool = False
    rewrites_git_history: bool = False
    deletes_data: bool = False
    runs_local_processes: bool = False
    accesses_network: bool = False
    accesses_paths_outside_repository: bool = False
    invokes_model: bool = False
    mutates_plans: bool = False
    mutates_task_status: bool = False
    creates_artifacts: bool = False
    requests_human_approval: bool = False
    accesses_secrets: bool = False
    requires_elevated_privileges: bool = False
    irreversible: bool = False


class ScopeKind(StrEnum):
    NONE = "none"
    REPOSITORY_WIDE_READ = "repository_wide_read"
    EXPLICIT_PATH_READ = "explicit_path_read"
    EXPLICIT_SOURCE_RANGE_READ = "explicit_source_range_read"
    AFFECTED_SCOPE_WRITE = "affected_scope_write"
    TEST_ONLY_WRITE = "test_only_write"
    DOCUMENTATION_ONLY_WRITE = "documentation_only_write"
    INFCTX_ONLY_WRITE = "infctx_only_write"
    GIT_METADATA = "git_metadata"
    EXTERNAL_FILESYSTEM = "external_filesystem"
    NETWORK_DESTINATION = "network_destination"


class ToolScope(StrictModel):
    kind: ScopeKind
    input_field: SafeName | None = None
    declared_values: tuple[str, ...] = Field(default=(), max_length=MAX_SCOPE_RULES)
    requires_resolved_task_context: bool = False


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalClass(StrEnum):
    NO_EXECUTION_AVAILABLE = "no_execution_available"
    DENIED = "denied"
    POLICY_PREAPPROVED_READ_ONLY = "policy_preapproved_read_only"
    PER_TASK_APPROVAL_REQUIRED = "per_task_approval_required"
    PER_INVOCATION_APPROVAL_REQUIRED = "per_invocation_approval_required"
    HUMAN_CONFIRMATION_REQUIRED = "human_confirmation_required"
    ADMINISTRATOR_POLICY_REQUIRED = "administrator_policy_required"
    PERMANENTLY_FORBIDDEN = "permanently_forbidden"


class ToolDefinition(StrictModel):
    registry_schema_version: Literal[1] = 1
    tool_id: ToolId
    canonical_name: SafeName
    display_name: str = Field(min_length=1, max_length=200)
    tool_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$", max_length=64)
    provider_family: SafeName
    description: str = Field(min_length=1, max_length=2000)
    category: ToolCategory
    implementation_status: ImplementationStatus
    availability: AvailabilityState
    definition_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    deprecated: bool = False
    replacement_tool_id: ToolId | None = None
    provenance: str = Field(min_length=1, max_length=500)
    created_at: datetime
    updated_at: datetime
    input_schema: ToolSchema = Field(default_factory=ToolSchema)
    output_schema: ToolSchema = Field(default_factory=ToolSchema)
    effects: ToolEffects
    required_capabilities: tuple[Capability, ...] = Field(default=(), max_length=32)
    scopes: tuple[ToolScope, ...] = Field(default=(), max_length=MAX_SCOPE_RULES)
    declared_risk: RiskLevel
    derived_risk: RiskLevel
    approval_class: ApprovalClass
    execution_handler: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_timestamps(self) -> ToolDefinition:
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("tool timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("tool updated_at cannot precede created_at")
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise ValueError("duplicate required capabilities are not allowed")
        if self.deprecated != (self.implementation_status == ImplementationStatus.DEPRECATED):
            raise ValueError("deprecation state and implementation status are inconsistent")
        return self


class ToolRegistryExport(StrictModel):
    schema_version: Literal[1] = 1
    registry_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_count: int = Field(ge=0)
    tools: tuple[ToolDefinition, ...]


class PolicyDecision(StrEnum):
    ELIGIBLE_FOR_FUTURE_REQUEST = "eligible_for_future_request"
    ELIGIBLE_WITH_HUMAN_APPROVAL = "eligible_with_human_approval"
    DENIED_CAPABILITY_NOT_REQUESTED = "denied_capability_not_requested"
    DENIED_CAPABILITY_NOT_GRANTED = "denied_capability_not_granted"
    DENIED_SCOPE_MISMATCH = "denied_scope_mismatch"
    DENIED_FORBIDDEN_SCOPE = "denied_forbidden_scope"
    DENIED_UNRESOLVED_CONTEXT = "denied_unresolved_context"
    DENIED_TASK_NOT_READY = "denied_task_not_ready"
    DENIED_TASK_OVERSIZED = "denied_task_oversized"
    DENIED_STALE_ANALYSIS = "denied_stale_analysis"
    DENIED_UNAPPROVED_PLAN = "denied_unapproved_plan"
    DENIED_TOOL_UNAVAILABLE = "denied_tool_unavailable"
    DENIED_TOOL_DEPRECATED = "denied_tool_deprecated"
    DENIED_POLICY = "denied_policy"
    PERMANENTLY_FORBIDDEN = "permanently_forbidden"
    INVALID_REQUEST = "invalid_request"


class ScopeCheck(StrictModel):
    kind: ScopeKind
    passed: bool
    requested_values: tuple[str, ...] = ()
    normalized_values: tuple[str, ...] = ()
    reason_code: str
    detail: str


class ToolPolicyDecision(StrictModel):
    schema_version: Literal[1] = 1
    decision_id: DecisionId
    semantic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_id: Literal["deterministic-tool-policy-v1"] = "deterministic-tool-policy-v1"
    policy_version: Literal[1] = 1
    risk_derivation_version: Literal[1] = 1
    registry_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str
    plan_revision: int = Field(ge=1)
    plan_revision_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    graph_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str
    task_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_status: str
    tool_id: ToolId
    tool_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_snapshot_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    task_context_analysis_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    context_fit_state: str
    requested_capabilities: tuple[Capability, ...]
    required_capabilities: tuple[Capability, ...]
    granted_capabilities: tuple[Capability, ...] = Field(max_length=0)
    missing_requested_capabilities: tuple[Capability, ...]
    missing_granted_capabilities: tuple[Capability, ...]
    scope_checks: tuple[ScopeCheck, ...]
    risk_level: RiskLevel
    approval_class: ApprovalClass
    decision: PolicyDecision
    structurally_eligible: bool
    executable_now: Literal[False] = False
    warnings: tuple[str, ...]
    reason_codes: tuple[str, ...]
    explanation: str
    remediation: tuple[str, ...]
    created_at: datetime

    @model_validator(mode="after")
    def validate_decision(self) -> ToolPolicyDecision:
        if self.created_at.tzinfo is None:
            raise ValueError("tool decision timestamp must be timezone-aware")
        if self.granted_capabilities:
            raise ValueError("G1 tool policy decisions cannot contain granted capabilities")
        return self


class DecisionStaleness(StrictModel):
    stale: bool
    reasons: tuple[str, ...]


JsonValue = str | int | float | bool | None | list[Any] | dict[str, Any]
