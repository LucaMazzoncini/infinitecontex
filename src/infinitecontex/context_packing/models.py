"""Immutable contracts for deterministic context ranking and packing."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from infinitecontex.context_budget.models import RetentionPriority, TokenCountProvenance
from infinitecontex.model_profiles.models import ModelIdentity

CONTEXT_MANIFEST_SCHEMA_VERSION = 1
RANKING_POLICY_ID = "deterministic-context-ranking-v1"
PACKING_STRATEGY_ID = "tiered-greedy-pack-v1"


class CandidateCategory(StrEnum):
    SYSTEM_INSTRUCTIONS = "system_instructions"
    PROJECT_INSTRUCTIONS = "project_instructions"
    CURRENT_TASK = "current_task"
    DIRECT_USER_REQUEST = "direct_user_request"
    SOURCE_CODE_EXCERPT = "source_code_excerpt"
    SYMBOL_DEFINITION = "symbol_definition"
    DEPENDENCY_CONTEXT = "dependency_context"
    TESTS = "tests"
    BUILD_OR_COMPILER_ERROR = "build_or_compiler_error"
    GIT_DIFF = "git_diff"
    DECISION_MEMORY = "decision_memory"
    TASK_STATE = "task_state"
    REPOSITORY_DOCUMENTATION = "repository_documentation"
    CONVERSATION_HISTORY = "conversation_history"
    TOOL_RESULT = "tool_result"
    MISCELLANEOUS = "miscellaneous"


class ChangeState(StrEnum):
    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    ADDED = "added"
    DELETED = "deleted"
    UNKNOWN = "unknown"


class ManifestDecision(StrEnum):
    PACKED = "packed"
    PACKED_WITH_WARNING = "packed_with_warning"
    OPTIONAL_EXCLUDED = "optional_excluded"
    MANDATORY_OVERFLOW = "mandatory_overflow"
    INVALID_REQUEST = "invalid_request"


class ExclusionReason(StrEnum):
    DUPLICATE = "duplicate"
    EXPLICITLY_EXCLUDED = "explicitly_excluded"
    BUDGET_EXCEEDED = "budget_exceeded"
    CATEGORY_CAP = "category_cap"
    MANDATORY_OVERFLOW = "mandatory_overflow"
    INVALID_REQUEST = "invalid_request"


class RelevanceSignals(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_relevance: int = Field(default=0, ge=0, le=1000)
    recency_rank: int = Field(default=0, ge=0, le=1000)
    source_confidence: int = Field(default=0, ge=0, le=1000)


class ContextCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1, max_length=240)
    category: CandidateCategory
    label: str = Field(min_length=1, max_length=500)
    content: str | None = None
    logical_source: str | None = None
    source_path: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    symbol_id: str | None = None
    token_count: int | None = Field(default=None, ge=0)
    mandatory: bool = False
    retention_priority: RetentionPriority = RetentionPriority.NORMAL
    relevance: RelevanceSignals = Field(default_factory=RelevanceSignals)
    observed_at: datetime | None = None
    dependency_distance: int | None = Field(default=None, ge=0, le=1000)
    direct_request_match: bool = False
    change_state: ChangeState = ChangeState.UNKNOWN
    deduplication_identity: str | None = None
    group_id: str | None = None
    tie_break_key: str = ""
    content_hash: str | None = None
    sensitivity: str | None = None
    excluded: bool = False
    exclusion_reason: str | None = None

    @model_validator(mode="after")
    def validate_candidate(self) -> "ContextCandidate":
        if self.content is None and self.token_count is None:
            raise ValueError("a referenced candidate without inline content requires token_count")
        if self.content is None and not any((self.logical_source, self.source_path, self.symbol_id)):
            raise ValueError("a candidate requires inline content or a stable source reference")
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("source ranges require both line_start and line_end")
        if self.line_start is not None and self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end cannot precede line_start")
        if self.observed_at is not None and self.observed_at.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        if self.mandatory and self.retention_priority == RetentionPriority.OPTIONAL:
            raise ValueError("a mandatory candidate cannot have optional retention priority")
        if self.excluded and not self.exclusion_reason:
            raise ValueError("an explicitly excluded candidate requires exclusion_reason")
        if self.mandatory and self.excluded:
            raise ValueError("a mandatory candidate cannot be explicitly excluded")
        return self


class RankingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: Literal["deterministic-context-ranking-v1"] = "deterministic-context-ranking-v1"
    version: Literal[1] = 1
    packing_strategy: Literal["tiered-greedy-pack-v1"] = "tiered-greedy-pack-v1"
    packing_version: Literal[1] = 1
    mandatory_handling: Literal["rank-first-include-all-or-overflow"] = "rank-first-include-all-or-overflow"
    duplicate_handling: Literal["equivalence-classes-audited-winner"] = "equivalence-classes-audited-winner"
    group_handling: Literal["metadata-only-no-group-cap-v1"] = "metadata-only-no-group-cap-v1"
    tie_break_order: tuple[str, ...] = (
        "tier",
        "score-descending",
        "explicit-tie-key",
        "candidate-id",
        "candidate-fingerprint",
    )
    direct_match_weight: int = 10000
    changed_file_weight: int = 1800
    test_error_weight: int = 1600
    dependency_base_weight: int = 1400
    dependency_step_penalty: int = 200
    task_relevance_weight: int = 4
    recency_weight: int = 1
    source_confidence_weight: int = 1
    retention_weight: int = 20
    optional_bulk_category_cap_basis_points: int = Field(default=6000, ge=1, le=10000)
    category_priority: dict[CandidateCategory, int] = Field(default_factory=lambda: _category_priority())


class RankedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    category: CandidateCategory
    label: str
    final_rank: int = Field(ge=1)
    tier: int = Field(ge=0)
    total_score: int
    component_scores: dict[str, int]
    tie_break_values: tuple[str, str, str]
    token_count: int = Field(ge=0)
    token_provenance: TokenCountProvenance
    estimation_strategy: str
    estimation_version: int | None = Field(default=None, ge=1)
    mandatory: bool
    direct_request_match: bool
    retention_priority: RetentionPriority
    group_id: str | None = None
    source_path: str | None = None
    source_range: str | None = None
    logical_source: str | None = None
    symbol_id: str | None = None
    content_hash: str
    candidate_fingerprint: str
    duplicate_state: Literal["unique"] = "unique"
    warnings: tuple[str, ...] = ()


class ManifestIncludedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: RankedCandidate
    inclusion_order: int = Field(ge=1)
    reason: str


class ManifestExcludedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    category: CandidateCategory
    label: str
    token_count: int = Field(ge=0)
    candidate_fingerprint: str
    reason: ExclusionReason
    detail: str
    duplicate_of: str | None = None


class ContextManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    manifest_id: str
    manifest_fingerprint: str
    calculated_at: datetime
    ranking_policy_id: str
    ranking_policy_version: int
    packing_strategy_id: str
    packing_strategy_version: int
    estimator_strategy: str
    estimator_version: int
    model_profile_id: str
    model_identity: ModelIdentity
    operational_context_tokens: int = Field(gt=0)
    maximum_recommended_input_tokens: int = Field(gt=0)
    caller_reserved_input_tokens: int = Field(ge=0)
    available_pack_tokens: int = Field(ge=0)
    mandatory_token_total: int = Field(ge=0)
    optional_token_total: int = Field(ge=0)
    included_token_total: int = Field(ge=0)
    remaining_pack_tokens: int = Field(ge=0)
    token_deficit: int = Field(default=0, ge=0)
    safeguards: dict[str, Any]
    included: tuple[ManifestIncludedCandidate, ...]
    excluded: tuple[ManifestExcludedCandidate, ...]
    warnings: tuple[str, ...]
    decision: ManifestDecision
    enforcement: Literal["inspection_only"] = "inspection_only"

    @model_validator(mode="after")
    def validate_manifest(self) -> "ContextManifest":
        if self.included_token_total > self.available_pack_tokens:
            raise ValueError("included tokens cannot exceed the available pack budget")
        if self.remaining_pack_tokens != self.available_pack_tokens - self.included_token_total:
            raise ValueError("remaining pack budget is inconsistent")
        if self.model_identity.model_digest is None:
            raise ValueError("a context manifest requires an exact model digest")
        if self.calculated_at.tzinfo is None:
            raise ValueError("calculated_at must include a timezone")
        return self


def _category_priority() -> dict[CandidateCategory, int]:
    return {
        CandidateCategory.SYSTEM_INSTRUCTIONS: 1000,
        CandidateCategory.PROJECT_INSTRUCTIONS: 950,
        CandidateCategory.CURRENT_TASK: 925,
        CandidateCategory.DIRECT_USER_REQUEST: 900,
        CandidateCategory.BUILD_OR_COMPILER_ERROR: 850,
        CandidateCategory.TESTS: 825,
        CandidateCategory.SYMBOL_DEFINITION: 800,
        CandidateCategory.SOURCE_CODE_EXCERPT: 775,
        CandidateCategory.GIT_DIFF: 750,
        CandidateCategory.DEPENDENCY_CONTEXT: 700,
        CandidateCategory.DECISION_MEMORY: 650,
        CandidateCategory.TASK_STATE: 625,
        CandidateCategory.REPOSITORY_DOCUMENTATION: 500,
        CandidateCategory.CONVERSATION_HISTORY: 350,
        CandidateCategory.TOOL_RESULT: 300,
        CandidateCategory.MISCELLANEOUS: 100,
    }
