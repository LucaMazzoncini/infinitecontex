"""Strict immutable contracts for allowlisted validation execution."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CommandCategory(StrEnum):
    TEST = "test"
    BUILD = "build"
    FORMAT = "format"
    LINT = "lint"
    STATIC_ANALYSIS = "static_analysis"


class ParameterKind(StrEnum):
    ENUM = "enum"
    BOUNDED_INTEGER = "bounded_integer"
    REPOSITORY_PATHS = "repository_paths"


class ValidationDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ExitClassification(StrEnum):
    PASSED = "passed"
    VALIDATION_FAILED = "validation_failed"
    PROCESS_ERROR = "process_error"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    REPOSITORY_MUTATION_DETECTED = "repository_mutation_detected"
    POLICY_REJECTED = "policy_rejected"
    INTERNAL_RUNNER_ERROR = "internal_runner_error"


class ParameterDefinition(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: ParameterKind
    flag: str | None = Field(default=None, pattern=r"^--[a-z][a-z0-9-]*$")
    enum_values: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    maximum_items: int = Field(default=1, ge=1, le=64)


class ValidationCommandDefinition(StrictModel):
    schema_version: Literal[1] = 1
    command_id: str = Field(pattern=r"^validation-command-[0-9a-f]{24}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_name: str = Field(pattern=r"^[a-z][a-z0-9.-]{1,99}$")
    display_name: str = Field(min_length=1, max_length=160)
    version: Literal["1.0.0"] = "1.0.0"
    category: CommandCategory
    description: str
    tool_name: str
    executable_adapter: Literal["current_python_module"] = "current_python_module"
    module: Literal["pytest", "ruff", "mypy", "build"]
    fixed_arguments: tuple[str, ...] = ()
    parameters: tuple[ParameterDefinition, ...] = ()
    working_directory: Literal["repository_root"] = "repository_root"
    allowed_environment_names: tuple[str, ...]
    timeout_seconds: int = Field(ge=1, le=1800)
    termination_grace_seconds: int = Field(ge=1, le=30)
    stdout_limit_bytes: int = Field(ge=1024, le=2 * 1024 * 1024)
    stderr_limit_bytes: int = Field(ge=1024, le=2 * 1024 * 1024)
    output_line_limit: int = Field(ge=100, le=50000)
    maximum_arguments: int = Field(ge=1, le=128)
    maximum_argument_length: int = Field(ge=1, le=1000)
    accepted_exit_codes: tuple[int, ...] = (0,)
    permitted_transient_prefixes: tuple[str, ...] = ()
    network_allowed: Literal[False] = False
    interactive_input: Literal[False] = False
    repository_mutation_policy: Literal["detect_and_contaminate"] = "detect_and_contaminate"
    evidence_type: Literal["validation_command"] = "validation_command"
    provenance: Literal["InfiniteContext G4 built-in catalog"] = "InfiniteContext G4 built-in catalog"
    implementation_status: Literal["available"] = "available"


class ExecutableIdentity(StrictModel):
    resolved_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)


class EnvironmentSummary(StrictModel):
    names: tuple[str, ...]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    removed_sensitive_names: tuple[str, ...] = ()


class ValidationProposal(StrictModel):
    schema_version: Literal[1] = 1
    proposal_id: str = Field(pattern=r"^validation-proposal-[0-9a-f]{24}$")
    semantic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str
    plan_revision: int
    graph_fingerprint: str
    task_id: str
    task_fingerprint: str
    repository_snapshot_fingerprint: str
    analysis_id: str
    analysis_fingerprint: str
    policy_decision_id: str
    policy_decision_fingerprint: str
    tool_id: str
    tool_version: str
    tool_fingerprint: str
    command_id: str
    command_version: str
    command_fingerprint: str
    executable: ExecutableIdentity
    arguments: tuple[str, ...]
    parameters: tuple[tuple[str, tuple[str, ...]], ...]
    arguments_fingerprint: str
    working_directory: Literal["."]
    environment: EnvironmentSummary
    timeout_seconds: int
    termination_grace_seconds: int
    stdout_limit_bytes: int
    stderr_limit_bytes: int
    output_line_limit: int
    repository_mutation_policy: str
    accepted_exit_codes: tuple[int, ...]
    requested_capability: str
    risk: str
    approval_required: Literal[True] = True
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    validation_passed: bool
    created_at: datetime


class ValidationAuthorization(StrictModel):
    schema_version: Literal[1] = 1
    authorization_id: str = Field(pattern=r"^validation-authorization-[0-9a-f]{24}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str
    proposal_fingerprint: str
    approval_id: str
    approval_fingerprint: str
    plan_id: str
    plan_revision: int
    task_id: str
    task_fingerprint: str
    repository_snapshot_fingerprint: str
    tool_fingerprint: str
    command_fingerprint: str
    executable_sha256: str
    arguments_fingerprint: str
    working_directory: Literal["."]
    environment_fingerprint: str
    timeout_seconds: int
    stdout_limit_bytes: int
    stderr_limit_bytes: int
    shell_allowed: Literal[False] = False
    network_allowed: Literal[False] = False
    reusable: Literal[False] = False


class ValidationApproval(StrictModel):
    schema_version: Literal[1] = 1
    approval_id: str = Field(pattern=r"^validation-approval-[0-9a-f]{24}$")
    approval_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str
    proposal_fingerprint: str
    plan_id: str
    plan_revision: int
    task_fingerprint: str
    repository_snapshot_fingerprint: str
    command_fingerprint: str
    actor: str = Field(min_length=1, max_length=160)
    decision: ValidationDecision
    reason: str | None = Field(default=None, max_length=1000)
    warnings_acknowledged: bool = False
    decided_at: datetime

    @field_validator("actor")
    @classmethod
    def actor_is_printable(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("actor must be printable and non-blank")
        return value


class StreamResult(StrictModel):
    byte_count: int = Field(ge=0)
    line_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview: str
    truncated: bool
    redacted: bool


class ValidationExecutionRecord(StrictModel):
    schema_version: Literal[1] = 1
    execution_id: str = Field(pattern=r"^tool-validation-[0-9a-f]{24}$")
    record_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str
    proposal_fingerprint: str
    approval_id: str
    approval_fingerprint: str
    plan_id: str
    plan_revision: int
    task_id: str
    task_fingerprint: str
    tool_id: str
    tool_fingerprint: str
    command_id: str
    command_fingerprint: str
    executable_sha256: str
    arguments_fingerprint: str
    environment_fingerprint: str
    repository_snapshot_before: str
    repository_snapshot_after: str
    started_at: datetime
    completed_at: datetime
    classification: ExitClassification
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    stdout: StreamResult
    stderr: StreamResult
    unexpected_changed_paths: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    evidence_id: str | None = None


class ValidationEvidence(StrictModel):
    schema_version: Literal[1] = 1
    evidence_id: str = Field(pattern=r"^validation-evidence-[0-9a-f]{24}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_id: str
    plan_id: str
    task_id: str
    criterion_id: str | None = None
    command_id: str
    repository_snapshot_before: str
    repository_snapshot_after: str
    classification: ExitClassification
    stdout_sha256: str
    stderr_sha256: str
    preview: str
    created_at: datetime
