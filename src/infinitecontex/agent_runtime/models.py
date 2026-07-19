from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_CHECKPOINT = "awaiting_human_checkpoint"
    AWAITING_MUTATION = "awaiting_mutation_review"
    COMPLETED = "completed_without_mutation"
    BUDGET_EXHAUSTED = "budget_exhausted"
    GRANT_EXHAUSTED = "grant_exhausted"
    GRANT_REVOKED = "grant_revoked"
    STALE = "stale"
    PROTOCOL_FAILED = "model_protocol_failed"
    MODEL_UNAVAILABLE = "model_unavailable"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RuntimeBudgets(StrictModel):
    maximum_model_calls: int = Field(default=24, ge=1, le=24)
    maximum_actions: int = Field(default=20, ge=1, le=20)
    maximum_invalid_responses: int = Field(default=3, ge=1, le=3)
    maximum_tool_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=2 * 1024 * 1024)
    maximum_output_tokens: int = Field(default=32_000, ge=1, le=32_000)
    maximum_wall_seconds: int = Field(default=1800, ge=1, le=1800)
    maximum_mutation_candidates: Literal[1] = 1


class AgentRun(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    semantic_fingerprint: str
    policy_version: Literal[1] = 1
    plan_id: str
    plan_revision: int
    task_id: str
    task_fingerprint: str
    grant_id: str
    grant_fingerprint: str
    session_id: str
    caller_type: Literal["supervised_local_agent"] = "supervised_local_agent"
    model_profile_id: str
    model_profile_fingerprint: str
    model_name: str
    model_digest: str
    repository_snapshot_fingerprint: str
    analysis_fingerprint: str
    budgets: RuntimeBudgets
    state: RunState
    model_calls: int = 0
    valid_responses: int = 0
    invalid_responses: int = 0
    dispatched_actions: int = 0
    returned_tool_bytes: int = 0
    created_at: datetime
    updated_at: datetime


class ToolRequest(StrictModel):
    schema_version: Literal[1] = 1
    kind: Literal["tool_request"]
    action_type: Literal["repo_files", "repo_read", "repo_read_range", "repo_path_search", "repo_literal_search"]
    arguments: dict[str, Any] = Field(max_length=32)
    rationale: str = Field(max_length=500)


class MutationCandidate(StrictModel):
    schema_version: Literal[1] = 1
    kind: Literal["mutation_candidate"]
    summary: str = Field(max_length=1000)
    operations: tuple[dict[str, Any], ...] = Field(max_length=64)
    suggested_validation_commands: tuple[str, ...] = Field(default=(), max_length=16)
    rationale: str = Field(max_length=500)


class Checkpoint(StrictModel):
    schema_version: Literal[1] = 1
    kind: Literal["checkpoint"]
    reason_code: str = Field(max_length=100)
    message: str = Field(max_length=500)


class FinalResponse(StrictModel):
    schema_version: Literal[1] = 1
    kind: Literal["final"]
    status: Literal["completed_without_mutation"]
    summary: str = Field(max_length=1000)
    unresolved_items: tuple[str, ...] = Field(default=(), max_length=32)


class AgentStep(StrictModel):
    schema_version: Literal[1] = 1
    step_id: str
    step_number: int
    prompt_fingerprint: str
    estimated_input_tokens: int
    reserved_output_tokens: int
    model_name: str
    model_digest: str
    generation_fingerprint: str
    raw_response_hash: str
    response_kind: str | None
    response_fingerprint: str | None
    schema_valid: bool
    action_request_id: str | None = None
    g7_record_id: str | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    started_at: datetime
    completed_at: datetime
