"""Immutable G7 authorization, grant, action, and session contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CallerType(StrEnum):
    HUMAN_CLI = "human_cli"
    FUTURE_AGENT = "future_agent"
    INTERNAL_SYSTEM = "internal_system"


class ActionKind(StrEnum):
    REPO_FILES = "repo_files"
    REPO_READ = "repo_read"
    REPO_READ_RANGE = "repo_read_range"
    REPO_PATH_SEARCH = "repo_path_search"
    REPO_LITERAL_SEARCH = "repo_literal_search"
    APPLY_MUTATION_PROPOSAL = "apply_mutation_proposal"
    RUN_VALIDATION_PROPOSAL = "run_validation_proposal"


class AuthorizationDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class GrantState(StrEnum):
    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    REVOKED = "revoked"
    STALE = "stale"
    CONSUMED_BY_MUTATION = "consumed_by_mutation"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


class SessionState(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    REVOKED = "revoked"
    STALE = "stale"
    EXHAUSTED = "exhausted"
    MUTATION_APPLIED = "mutation_applied"


class ActionStatus(StrEnum):
    COMPLETED = "action_completed"
    FAILED = "action_failed"
    REJECTED = "action_rejected"
    MUTATION_APPLIED = "mutation_applied"
    VALIDATION_COMPLETED = "validation_completed"
    READ_COMPLETED = "read_completed"


class AuthorizationSpecification(StrictModel):
    schema_version: Literal[1] = 1
    purpose: str = Field(min_length=1, max_length=500)
    allowed_tools: tuple[str, ...] = Field(min_length=1, max_length=8)
    allowed_actions: tuple[ActionKind, ...] = Field(min_length=1, max_length=7)
    read_scopes: tuple[str, ...] = Field(default=(), max_length=128)
    write_scopes: tuple[str, ...] = Field(default=(), max_length=16)
    forbidden_scopes: tuple[str, ...] = Field(default=(), max_length=128)
    permitted_mutation_proposals: tuple[str, ...] = Field(default=(), max_length=16)
    permitted_validation_proposals: tuple[str, ...] = Field(default=(), max_length=32)
    maximum_total_actions: int = Field(ge=1, le=1000)
    maximum_actions_per_tool: dict[str, int] = Field(min_length=1, max_length=8)
    maximum_read_bytes: int = Field(default=1024 * 1024, ge=1, le=64 * 1024 * 1024)
    maximum_search_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=64 * 1024 * 1024)
    maximum_output_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=8 * 1024 * 1024)
    maximum_mutation_actions: int = Field(default=0, ge=0, le=1)
    maximum_validation_actions: int = Field(default=0, ge=0, le=32)
    repository_snapshot_policy: Literal["exact_until_change"] = "exact_until_change"
    continuation_policy: Literal["stop_on_failure", "explicit_continue"] = "stop_on_failure"
    requested_actor: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("allowed_tools", "read_scopes", "write_scopes", "forbidden_scopes", mode="after")
    @classmethod
    def unique_sorted(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or item in {"*", "all", "**"} for item in value):
            raise ValueError("wildcard or empty authorization values are forbidden")
        if len(set(value)) != len(value):
            raise ValueError("authorization collections must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_limits(self) -> "AuthorizationSpecification":
        if set(self.maximum_actions_per_tool) != set(self.allowed_tools):
            raise ValueError("per-tool allowances must exactly cover allowed tools")
        if sum(self.maximum_actions_per_tool.values()) < self.maximum_total_actions:
            raise ValueError("per-tool allowances cannot be smaller than total allowance")
        return self


class ToolAuthorization(StrictModel):
    tool_id: str
    tool_version: str
    definition_fingerprint: str
    canonical_name: str


class AuthorizationProposal(StrictModel):
    schema_version: Literal[1] = 1
    proposal_id: str
    semantic_fingerprint: str
    policy_version: Literal[1] = 1
    plan_id: str
    plan_revision: int
    graph_fingerprint: str
    task_id: str
    task_fingerprint: str
    task_status: str
    requested_capabilities: tuple[str, ...]
    affected_scopes: tuple[str, ...]
    forbidden_scopes: tuple[str, ...]
    analysis_id: str
    analysis_fingerprint: str
    repository_snapshot_fingerprint: str
    tools: tuple[ToolAuthorization, ...]
    allowed_actions: tuple[ActionKind, ...]
    read_scopes: tuple[str, ...]
    write_scopes: tuple[str, ...]
    permitted_mutation_proposals: tuple[str, ...]
    permitted_validation_proposals: tuple[str, ...]
    maximum_total_actions: int
    maximum_actions_per_tool: dict[str, int]
    maximum_read_bytes: int
    maximum_search_bytes: int
    maximum_output_bytes: int
    maximum_mutation_actions: int
    maximum_validation_actions: int
    repository_snapshot_policy: str
    continuation_policy: str
    risk: str
    warnings: tuple[str, ...] = ()
    created_at: datetime


class AuthorizationApproval(StrictModel):
    schema_version: Literal[1] = 1
    approval_id: str
    approval_fingerprint: str
    proposal_id: str
    proposal_fingerprint: str
    plan_id: str
    plan_revision: int
    task_id: str
    task_fingerprint: str
    actor: str = Field(min_length=1, max_length=200)
    actor_type: Literal["human", "imported_authority"] = "human"
    decision: AuthorizationDecision
    reason: str = Field(min_length=1, max_length=1000)
    warnings_acknowledged: bool = False
    authorized_tools: tuple[str, ...]
    authorized_scopes: tuple[str, ...]
    maximum_total_actions: int
    decided_at: datetime


class ExecutionGrant(StrictModel):
    schema_version: Literal[1] = 1
    grant_id: str
    grant_fingerprint: str
    proposal_id: str
    proposal_fingerprint: str
    approval_id: str
    approval_fingerprint: str
    plan_id: str
    plan_revision: int
    task_id: str
    task_fingerprint: str
    task_status_at_activation: str
    repository_snapshot_fingerprint: str
    analysis_fingerprint: str
    tools: tuple[ToolAuthorization, ...]
    allowed_actions: tuple[ActionKind, ...]
    read_scopes: tuple[str, ...]
    write_scopes: tuple[str, ...]
    permitted_mutation_proposals: tuple[str, ...]
    permitted_validation_proposals: tuple[str, ...]
    maximum_total_actions: int
    maximum_actions_per_tool: dict[str, int]
    maximum_read_bytes: int
    maximum_search_bytes: int
    maximum_output_bytes: int
    maximum_mutation_actions: int
    maximum_validation_actions: int
    activated_at: datetime


class GrantStateRecord(StrictModel):
    schema_version: Literal[1] = 1
    grant_id: str
    state: GrantState
    remaining_total_actions: int = Field(ge=0)
    remaining_actions_per_tool: dict[str, int]
    remaining_read_bytes: int = Field(ge=0)
    remaining_search_bytes: int = Field(ge=0)
    remaining_output_bytes: int = Field(ge=0)
    remaining_mutation_actions: int = Field(ge=0)
    remaining_validation_actions: int = Field(ge=0)
    version: int = Field(ge=1)
    updated_at: datetime


class ActionRequest(StrictModel):
    schema_version: Literal[1] = 1
    action_request_id: str = Field(pattern=r"^action-[A-Za-z0-9_.-]{1,100}$")
    semantic_fingerprint: str | None = None
    grant_id: str
    grant_fingerprint: str
    plan_id: str
    plan_revision: int
    task_id: str
    task_fingerprint: str
    tool_id: str
    tool_version: str
    action: ActionKind
    caller_type: CallerType = CallerType.HUMAN_CLI
    input: dict[str, Any] = Field(default_factory=dict, max_length=32)
    requested_read_bytes: int = Field(default=0, ge=0)
    requested_search_bytes: int = Field(default=0, ge=0)
    requested_output_bytes: int = Field(default=0, ge=0)
    correlation_id: str | None = Field(default=None, max_length=200)
    created_at: datetime


class ActionJournalEntry(StrictModel):
    schema_version: Literal[1] = 1
    action_request_id: str
    action_fingerprint: str
    reservation_id: str
    tool_id: str
    action: ActionKind
    normalized_input_fingerprint: str
    snapshot_before: str
    snapshot_after: str | None = None
    underlying_record_id: str | None = None
    status: ActionStatus
    allowance_consumed: bool
    output_count: int = 0
    byte_count: int = 0
    started_at: datetime
    completed_at: datetime
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class ExecutionSession(StrictModel):
    schema_version: Literal[1] = 1
    session_id: str
    session_fingerprint: str
    grant_id: str
    grant_fingerprint: str
    plan_id: str
    plan_revision: int
    task_id: str
    task_fingerprint: str
    state: SessionState
    action_count: int = 0
    initial_snapshot_fingerprint: str
    current_snapshot_fingerprint: str
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class GrantRevocation(StrictModel):
    schema_version: Literal[1] = 1
    revocation_id: str
    revocation_fingerprint: str
    grant_id: str
    grant_fingerprint: str
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    revoked_at: datetime
