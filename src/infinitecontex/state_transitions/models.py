"""Immutable G6 plan-state transition contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from infinitecontex.evidence_review.models import ActorType, Confidence, ReviewOutcome
from infinitecontex.planning.models import CriterionStatus, TaskStatus


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EligibilityOutcome(StrEnum):
    ELIGIBLE = "eligible"
    ELIGIBLE_WITH_WARNING = "eligible_with_warning"
    NO_CHANGE_REQUIRED = "no_change_required"
    REVIEW_MISSING = "review_missing"
    REVIEW_NOT_ACCEPTED = "review_not_accepted"
    REVIEW_STALE = "review_stale"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    EVIDENCE_FAILED = "evidence_failed"
    EVIDENCE_CONFLICTING = "evidence_conflicting"
    CRITERION_TRANSITION_FORBIDDEN = "criterion_transition_forbidden"
    TASK_TRANSITION_FORBIDDEN = "task_transition_forbidden"
    MANDATORY_CRITERIA_INCOMPLETE = "mandatory_criteria_incomplete"
    BLOCKING_DEPENDENCIES = "blocking_dependencies"
    PLAN_REVISION_STALE = "plan_revision_stale"
    TASK_FINGERPRINT_STALE = "task_fingerprint_stale"
    REPOSITORY_STATE_STALE = "repository_state_stale"
    INVALID_PROPOSAL = "invalid_proposal"
    INVALID_REQUEST = "invalid_request"


class TransitionDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class CriterionTransitionRequest(FrozenModel):
    criterion_id: str = Field(min_length=1, max_length=160)
    target_status: CriterionStatus
    review_id: str | None = None
    decision_id: str | None = None
    reason: str | None = Field(default=None, max_length=2000)
    warnings_acknowledged: bool = False


class CriterionTransitionEntry(FrozenModel):
    criterion_id: str
    criterion_fingerprint: str
    current_status: CriterionStatus
    proposed_status: CriterionStatus
    transition_rule: str
    review_id: str | None = None
    review_fingerprint: str | None = None
    decision_id: str | None = None
    decision_fingerprint: str | None = None
    review_outcome: ReviewOutcome | None = None
    confidence: Confidence | None = None
    human_only_verification: bool = False
    mandatory: bool
    reason: str | None = None
    warnings: tuple[str, ...] = ()
    entry_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class StateTransitionProposal(FrozenModel):
    schema_version: Literal[1] = 1
    proposal_id: str
    semantic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    transition_policy_version: Literal[1] = 1
    transition_policy_fingerprint: str
    plan_id: str
    source_plan_revision: int = Field(ge=1)
    source_revision_fingerprint: str
    source_graph_fingerprint: str
    task_id: str
    task_fingerprint: str
    source_task_status: TaskStatus
    proposed_task_status: TaskStatus | None = None
    criterion_transitions: tuple[CriterionTransitionEntry, ...]
    accepted_review_ids: tuple[str, ...] = ()
    accepted_review_fingerprints: tuple[str, ...] = ()
    review_decision_ids: tuple[str, ...] = ()
    review_decision_fingerprints: tuple[str, ...] = ()
    repository_snapshot_fingerprints: tuple[str, ...] = ()
    task_context_analysis_fingerprint: str | None = None
    validation_evidence_ids: tuple[str, ...] = ()
    validation_execution_ids: tuple[str, ...] = ()
    before_state_fingerprint: str
    proposed_after_state_fingerprint: str
    resulting_graph_fingerprint_preview: str
    resulting_revision: int = Field(ge=2)
    eligibility: EligibilityOutcome
    validation_checks: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    remediation: tuple[str, ...] = ()
    approval_required: Literal[True] = True
    created_at: datetime


class StateTransitionApproval(FrozenModel):
    schema_version: Literal[1] = 1
    approval_id: str
    approval_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str
    proposal_fingerprint: str
    plan_id: str
    source_plan_revision: int
    task_id: str
    task_fingerprint: str
    criterion_fingerprints: tuple[str, ...]
    review_decision_fingerprints: tuple[str, ...]
    proposed_after_state_fingerprint: str
    actor: str = Field(min_length=1, max_length=200)
    actor_type: ActorType
    decision: TransitionDecision
    reason: str = Field(min_length=1, max_length=2000)
    warnings_acknowledged: bool = False
    decided_at: datetime

    @field_validator("actor")
    @classmethod
    def human_actor(cls, value: str) -> str:
        if value.strip().casefold() in {"llm", "model", "ollama", "agent"}:
            raise ValueError("actor must identify a human reviewer")
        return value.strip()


class CriterionStatusChange(FrozenModel):
    criterion_id: str
    before: CriterionStatus
    after: CriterionStatus


class StateTransitionApplication(FrozenModel):
    schema_version: Literal[1] = 1
    application_id: str
    application_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str
    proposal_fingerprint: str
    approval_id: str
    approval_fingerprint: str
    plan_id: str
    source_revision: int
    resulting_revision: int
    source_revision_fingerprint: str
    resulting_revision_fingerprint: str
    actor: str
    task_id: str
    task_status_before: TaskStatus
    task_status_after: TaskStatus
    criterion_changes: tuple[CriterionStatusChange, ...]
    review_ids: tuple[str, ...]
    before_state_fingerprint: str
    after_state_fingerprint: str
    resulting_graph_fingerprint: str
    applied_at: datetime
    source_files_changed: Literal[False] = False
    execution_authorized: Literal[False] = False
    capabilities_granted: Literal[False] = False
