"""Immutable split proposal, coverage, lineage, and approval contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from infinitecontex.planning.models import Capability, PlanInput, TaskInput
from infinitecontex.task_context.models import TaskContextAnalysis


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SplitEligibility(StrEnum):
    NOT_REQUIRED = "not_required"
    ELIGIBLE = "eligible"
    ELIGIBLE_WITH_WARNINGS = "eligible_with_warnings"
    ALREADY_ATOMIC = "already_atomic"
    INSUFFICIENT_STRUCTURE = "insufficient_structure"
    REQUIRED_CONTEXT_UNRESOLVED = "required_context_unresolved"
    SCOPE_CONFLICT = "scope_conflict"
    UNSUPPORTED_REQUIRED_SYMBOL = "unsupported_required_symbol"
    PROFILE_UNAVAILABLE = "profile_unavailable"
    ANALYSIS_STALE = "analysis_stale"
    SPLIT_DEPTH_EXCEEDED = "split_depth_exceeded"
    CHILD_LIMIT_EXCEEDED = "child_limit_exceeded"
    UNSPLITTABLE_OVERSIZED = "unsplittable_oversized"
    INVALID_TASK = "invalid_task"
    INVALID_PLAN = "invalid_plan"


class SplitRule(StrEnum):
    EXPLICIT_GROUP = "explicit_group"
    AFFECTED_SCOPE = "affected_scope"
    EXPECTED_OUTPUT = "expected_output"
    LIFECYCLE_PHASE = "lifecycle_phase"
    CRITERION_GROUP = "criterion_group"
    REQUIRED_CONTEXT_GROUP = "required_context_group"


class CoverageDisposition(StrEnum):
    ASSIGNED = "assigned"
    SHARED = "shared"
    RETAINED = "retained_on_coordination_parent"
    TRANSFORMED = "transformed"
    UNRESOLVED = "unresolved"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ApprovalActorType(StrEnum):
    HUMAN = "human"
    IMPORTED_EXTERNAL = "imported_external"
    FUTURE_POLICY_AUTHORITY = "future_policy_authority"


class ApplicationStatus(StrEnum):
    NOT_APPLIED = "not_applied"
    APPLIED = "applied"


class RuleCandidate(StrictModel):
    rule: SplitRule
    priority: int = Field(ge=1)
    eligible: bool
    partition_count: int = Field(ge=0)
    reason: str


class CoverageRecord(StrictModel):
    category: str
    source_key: str
    disposition: CoverageDisposition
    child_keys: tuple[str, ...] = ()
    detail: str


class DependencyRewrite(StrictModel):
    dependent_task_key: str
    old_dependency_key: str
    new_dependency_keys: tuple[str, ...]
    kind: Literal["incoming", "outgoing", "internal", "unchanged"]


class ProposedChild(StrictModel):
    proposed_task_id: str
    stable_child_key: str
    parent_source_task_id: str
    split_depth: int = Field(ge=1)
    split_dimension: SplitRule
    split_partition_key: str
    task: TaskInput
    deterministic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_fit: TaskContextAnalysis
    leaf: bool = True
    requested_capabilities: tuple[Capability, ...]
    granted_capabilities: tuple[Capability, ...] = Field(max_length=0)


class ContractCoverageReport(StrictModel):
    records: tuple[CoverageRecord, ...]
    complete: bool
    unresolved_count: int = Field(ge=0)
    shared_record_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_complete(self) -> "ContractCoverageReport":
        unresolved = sum(item.disposition == CoverageDisposition.UNRESOLVED for item in self.records)
        if unresolved != self.unresolved_count or self.complete != (unresolved == 0):
            raise ValueError("contract coverage summary is inconsistent")
        return self


class SplitProposal(StrictModel):
    schema_version: Literal[1] = 1
    proposal_id: str = Field(pattern=r"^split-proposal-[0-9a-f]{24}$")
    semantic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    split_policy_id: str
    split_policy_version: int
    plan_id: str
    source_plan_revision: int = Field(ge=1)
    source_revision_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_graph_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_task_id: str
    source_task_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_snapshot_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str
    model_digest: str
    originating_analysis_id: str
    originating_analysis_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    eligibility: SplitEligibility
    optional_proposal: bool
    selected_rule: SplitRule
    split_rule_version: Literal[1] = 1
    rule_candidates: tuple[RuleCandidate, ...]
    proposed_children: tuple[ProposedChild, ...]
    completion_barrier_key: str
    dependency_rewrites: tuple[DependencyRewrite, ...]
    contract_coverage: ContractCoverageReport
    shared_contract_fields: tuple[str, ...]
    shared_context_duplicate_tokens: int = Field(ge=0)
    parent_replacement_strategy: Literal["supersede_with_completion_barrier"] = (
        "supersede_with_completion_barrier"
    )
    proposed_plan: PlanInput
    resulting_graph_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    resulting_task_count: int = Field(ge=1)
    total_leaf_tasks: int = Field(ge=1)
    maximum_split_depth: int = Field(ge=1)
    every_leaf_fits: bool
    validation_passed: bool
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    remediation: tuple[str, ...]
    created_at: datetime

    @model_validator(mode="after")
    def validate_timestamp(self) -> "SplitProposal":
        if self.created_at.tzinfo is None:
            raise ValueError("proposal timestamp must be timezone-aware")
        if not self.proposed_children:
            raise ValueError("a split proposal requires at least one validated leaf")
        if self.total_leaf_tasks != len(self.proposed_children):
            raise ValueError("split proposal leaf count is inconsistent")
        if self.every_leaf_fits != all(item.context_fit.passing for item in self.proposed_children):
            raise ValueError("split proposal fit summary is inconsistent")
        if self.validation_passed != (
            self.every_leaf_fits and self.contract_coverage.complete and not self.errors
        ):
            raise ValueError("split proposal validation summary is inconsistent")
        if len({item.stable_child_key for item in self.proposed_children}) != len(
            self.proposed_children
        ):
            raise ValueError("split proposal child keys must be unique")
        return self


class SplitApproval(StrictModel):
    schema_version: Literal[1] = 1
    approval_id: str = Field(pattern=r"^split-approval-[0-9a-f]{24}$")
    approval_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str
    proposal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str
    source_revision: int = Field(ge=1)
    proposed_graph_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: ApprovalDecision
    actor_identifier: str = Field(min_length=1, max_length=160)
    actor_type: ApprovalActorType
    decision_reason: str = Field(min_length=1, max_length=1000)
    decided_at: datetime
    repository_snapshot_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str
    model_digest: str
    warnings_acknowledged: bool = False
    applied_revision_number: int | None = Field(default=None, ge=1)
    application_status: ApplicationStatus

    @field_validator("actor_identifier", "decision_reason")
    @classmethod
    def printable(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("approval actor and reason must not contain control characters")
        return value.strip()

    @model_validator(mode="after")
    def validate_approval(self) -> "SplitApproval":
        if self.decided_at.tzinfo is None:
            raise ValueError("approval timestamp must be timezone-aware")
        if self.actor_type == ApprovalActorType.FUTURE_POLICY_AUTHORITY:
            raise ValueError("future policy authority cannot approve this milestone")
        applied = self.application_status == ApplicationStatus.APPLIED
        if applied != (self.applied_revision_number is not None):
            raise ValueError("approval application fields are inconsistent")
        if applied and self.decision != ApprovalDecision.APPROVED:
            raise ValueError("only an approved split can be applied")
        return self
