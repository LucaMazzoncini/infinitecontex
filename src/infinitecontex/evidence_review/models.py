"""Immutable G5 evidence review and human-decision contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewOutcome(StrEnum):
    SUPPORTED = "supported"
    SUPPORTED_WITH_WARNING = "supported_with_warning"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    IRRELEVANT_EVIDENCE = "irrelevant_evidence"
    STALE_EVIDENCE = "stale_evidence"
    CONTAMINATED_EVIDENCE = "contaminated_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    DUPLICATE_EVIDENCE = "duplicate_evidence"
    MISSING_EVIDENCE = "missing_evidence"
    CRITERION_UNSUPPORTED = "criterion_unsupported"
    INVALID_EVIDENCE = "invalid_evidence"
    INVALID_CRITERION = "invalid_criterion"
    INVALID_REQUEST = "invalid_request"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class AggregationRule(StrEnum):
    ALL_REQUIRED = "all_required"
    ANY_ONE = "any_one"
    MINIMUM_COUNT = "minimum_count"
    ALL_TARGETS = "all_targets"
    LATEST_COMPLETE_SET = "latest_complete_set"


class FreshnessResult(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MIXED = "mixed"
    NOT_EVALUATED = "not_evaluated"


class IntegrityResult(StrEnum):
    VERIFIED = "verified"
    WARNING = "warning"
    FAILED = "failed"
    NOT_EVALUATED = "not_evaluated"


class ReviewDecisionValue(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ActorType(StrEnum):
    HUMAN = "human"
    IMPORTED_EXTERNAL_REVIEWER = "imported_external_reviewer"
    FUTURE_POLICY_AUTHORITY = "future_policy_authority"


class EvidenceContribution(StrictModel):
    evidence_id: str
    evidence_fingerprint: str
    execution_id: str
    execution_fingerprint: str
    command_id: str
    command_fingerprint: str
    repository_snapshot: str
    classification: str
    disposition: Literal["accepted", "rejected", "duplicate", "conflicting"]
    reason_codes: tuple[str, ...]
    output_complete: bool
    redacted: bool
    contaminated: bool


class EvidenceReview(StrictModel):
    schema_version: Literal[1] = 1
    review_id: str = Field(pattern=r"^evidence-review-[0-9a-f]{24}$")
    semantic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_policy_version: Literal[1] = 1
    review_policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str
    plan_revision: int = Field(ge=1)
    graph_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str
    task_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_context_analysis_fingerprint: str | None = None
    criterion_id: str
    criterion_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    criterion_status_before: str
    required_evidence_type: str
    verification_method: str
    mandatory: bool
    evidence_ids: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    repository_snapshot_fingerprints: tuple[str, ...]
    command_definition_ids: tuple[str, ...]
    command_definition_fingerprints: tuple[str, ...]
    validation_execution_ids: tuple[str, ...]
    validation_execution_fingerprints: tuple[str, ...]
    evidence_provenance: tuple[str, ...]
    outcome: ReviewOutcome
    evidence_supports_criterion: bool
    criterion_status_unchanged: Literal[True] = True
    task_status_unchanged: Literal[True] = True
    evidence_count: int = Field(ge=0, le=1000)
    accepted_evidence_ids: tuple[str, ...] = ()
    rejected_evidence_ids: tuple[str, ...] = ()
    duplicate_evidence_ids: tuple[str, ...] = ()
    conflicting_evidence_ids: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    freshness_result: FreshnessResult
    repository_state_result: IntegrityResult
    command_identity_result: IntegrityResult
    output_integrity_result: IntegrityResult
    contamination_result: IntegrityResult
    aggregation_rule: AggregationRule
    aggregation_result: str
    confidence: Confidence
    contributions: tuple[EvidenceContribution, ...]
    reason_codes: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    remediation: tuple[str, ...] = ()
    created_at: datetime


class EvidenceReviewDecision(StrictModel):
    schema_version: Literal[1] = 1
    decision_id: str = Field(pattern=r"^evidence-review-decision-[0-9a-f]{24}$")
    decision_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_id: str
    review_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str
    plan_revision: int
    task_id: str
    task_fingerprint: str
    criterion_id: str
    criterion_fingerprint: str
    actor: str = Field(min_length=1, max_length=160)
    actor_type: ActorType
    decision: ReviewDecisionValue
    reason: str = Field(min_length=1, max_length=1000)
    warnings_acknowledged: bool = False
    decided_at: datetime
    criterion_status_unchanged: Literal[True] = True
    task_status_unchanged: Literal[True] = True
    completes_criterion_or_task: Literal[False] = False

    @field_validator("actor")
    @classmethod
    def actor_is_valid(cls, value: str) -> str:
        value = value.strip()
        if (
            not value
            or any(ord(char) < 32 for char in value)
            or value.casefold() in {"llm", "model", "ollama", "agent"}
        ):
            raise ValueError("review actor must be a printable non-model identity")
        return value
