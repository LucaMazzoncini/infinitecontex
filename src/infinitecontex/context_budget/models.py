"""Immutable contracts for deterministic context-budget inspection."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from infinitecontex.model_profiles.models import ModelIdentity


class ContextSectionCategory(StrEnum):
    SYSTEM_INSTRUCTIONS = "system_instructions"
    PROJECT_INSTRUCTIONS = "project_instructions"
    CONVERSATION_HISTORY = "conversation_history"
    CURRENT_USER_REQUEST = "current_user_request"
    RETRIEVED_PROJECT_CONTEXT = "retrieved_project_context"
    SOURCE_CODE_EXCERPTS = "source_code_excerpts"
    TASK_STATE = "task_state"
    TOOL_DEFINITIONS = "tool_definitions"
    TOOL_RESULTS = "tool_results"
    PERSISTENT_MEMORY = "persistent_memory"
    OTHER = "other"


class RetentionPriority(IntEnum):
    OPTIONAL = 10
    NORMAL = 50
    HIGH = 80
    REQUIRED = 100


class TokenCountProvenance(StrEnum):
    MEASURED = "measured"
    HEURISTIC = "heuristic"


class EstimationConfidence(StrEnum):
    NONE = "none"
    HIGH = "high"
    LOW = "low"
    MIXED = "mixed"


class BudgetDecision(StrEnum):
    PASS_TARGET = "pass_target"
    PASS_HARD = "pass_hard"
    REPACK_REQUIRED = "repack_required"
    SPLIT_REQUIRED = "split_required"
    BLOCKED = "blocked"


class ContextSectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    category: ContextSectionCategory
    text: str | None = None
    token_count: int | None = Field(default=None, ge=0)
    source_ref: str | None = None
    mandatory: bool = True
    priority: RetentionPriority = RetentionPriority.NORMAL

    @model_validator(mode="after")
    def require_one_count_source(self) -> "ContextSectionInput":
        if (self.text is None) == (self.token_count is None):
            raise ValueError("a context section requires exactly one of text or token_count")
        if self.mandatory and self.priority == RetentionPriority.OPTIONAL:
            raise ValueError("a mandatory section cannot have optional retention priority")
        return self


class ContextSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    category: ContextSectionCategory
    token_count: int = Field(ge=0)
    count_provenance: TokenCountProvenance
    estimation_strategy: str
    estimation_version: int | None = Field(default=None, ge=1)
    normalization: str | None = None
    conservatism: str | None = None
    source_ref: str | None = None
    mandatory: bool
    priority: RetentionPriority


class BudgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    model_identity: ModelIdentity
    sections: tuple[ContextSection, ...]
    requested_output_tokens: int = Field(ge=0)
    requested_tool_result_tokens: int = Field(ge=0)
    calculated_at: datetime
    estimation_strategy: str

    @model_validator(mode="after")
    def require_timezone(self) -> "BudgetRequest":
        if self.calculated_at.tzinfo is None:
            raise ValueError("calculated_at must include a timezone")
        return self


class FixedReserves(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output_tokens: int = Field(ge=0)
    tool_result_tokens: int = Field(ge=0)
    system_prompt_tokens: int = Field(ge=0)
    safety_margin_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class BudgetResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    model_identity: ModelIdentity
    operational_context_tokens: int
    configured_context_tokens: int
    advertised_context_tokens: int | None
    fixed_reserves: FixedReserves
    maximum_recommended_input_tokens: int
    total_proposed_input_tokens: int
    remaining_input_tokens: int
    utilization_basis_points: int
    sections: tuple[ContextSection, ...]
    estimation_confidence: EstimationConfidence
    warnings: tuple[str, ...]
    decision: BudgetDecision
    calculated_at: datetime
    enforcement: Literal["inspection_only"] = "inspection_only"
