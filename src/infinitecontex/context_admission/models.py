"""Immutable contracts for fail-closed runtime context admission."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from infinitecontex.context_budget.models import FixedReserves, TokenCountProvenance
from infinitecontex.model_profiles.models import ModelIdentity, normalize_model_name

ADMISSION_POLICY_ID = "fail-closed-context-admission-v1"
ADMISSION_RECORD_SCHEMA_VERSION = 1


class AdmissionSectionKind(StrEnum):
    SYSTEM_INSTRUCTIONS = "system_instructions"
    PROJECT_INSTRUCTIONS = "project_instructions"
    MANIFEST_CONTEXT = "manifest_context"
    CONVERSATION_HISTORY = "conversation_history"
    CURRENT_USER_REQUEST = "current_user_request"
    TOOL_DEFINITIONS = "tool_definitions"
    TOOL_RESULTS = "tool_results"
    ADDITIONAL_RUNTIME = "additional_runtime"
    PROVIDER_OVERHEAD = "provider_overhead"


class AdmissionDecision(StrEnum):
    ADMITTED = "admitted"
    ADMITTED_WITH_WARNING = "admitted_with_warning"
    REJECTED_MISSING_PROFILE = "rejected_missing_profile"
    REJECTED_PROFILE_MISMATCH = "rejected_profile_mismatch"
    REJECTED_WEAK_IDENTITY = "rejected_weak_identity"
    REJECTED_STALE_PROFILE = "rejected_stale_profile"
    REJECTED_MISSING_MANIFEST = "rejected_missing_manifest"
    REJECTED_INVALID_MANIFEST = "rejected_invalid_manifest"
    REJECTED_MANIFEST_MISMATCH = "rejected_manifest_mismatch"
    REJECTED_CONTENT_MISMATCH = "rejected_content_mismatch"
    REJECTED_BUDGET_OVERFLOW = "rejected_budget_overflow"
    REJECTED_ALLOWANCE_OVERFLOW = "rejected_allowance_overflow"
    REJECTED_UNACCOUNTED_CONTENT = "rejected_unaccounted_content"
    REJECTED_POLICY = "rejected_policy"
    INVALID_REQUEST = "invalid_request"


class AdmissionSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: str = Field(min_length=1, max_length=240)
    kind: AdmissionSectionKind
    role: Literal["system", "user", "assistant"]
    content: str
    mandatory: bool = True
    manifest_candidate_id: str | None = None
    manifest_candidate_fingerprint: str | None = None

    @model_validator(mode="after")
    def validate_manifest_link(self) -> "AdmissionSection":
        if (self.manifest_candidate_id is None) != (self.manifest_candidate_fingerprint is None):
            raise ValueError("manifest candidate ID and fingerprint must be supplied together")
        return self


class AdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    normalized_model_name: str = Field(min_length=1)
    model_digest: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    manifest_id: str = Field(min_length=1)
    system_instructions: AdmissionSection
    project_instructions: tuple[AdmissionSection, ...] = ()
    manifest_context: tuple[AdmissionSection, ...] = ()
    conversation_history: tuple[AdmissionSection, ...] = ()
    tool_definitions: tuple[AdmissionSection, ...] = ()
    tool_results: tuple[AdmissionSection, ...] = ()
    additional_runtime_sections: tuple[AdmissionSection, ...] = ()
    current_user_request: AdmissionSection
    requested_output_tokens: int = Field(ge=0)
    requested_tool_result_tokens: int = Field(ge=0)
    calculated_at: datetime
    admission_policy_id: Literal["fail-closed-context-admission-v1"] = "fail-closed-context-admission-v1"
    admission_policy_version: Literal[1] = 1
    correlation_id: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def validate_request(self) -> "AdmissionRequest":
        if self.normalized_model_name != normalize_model_name(self.model_name):
            raise ValueError("normalized_model_name does not match model_name")
        if self.calculated_at.tzinfo is None:
            raise ValueError("calculated_at must include a timezone")
        if self.system_instructions.kind != AdmissionSectionKind.SYSTEM_INSTRUCTIONS:
            raise ValueError("system_instructions has the wrong section kind")
        if self.current_user_request.kind != AdmissionSectionKind.CURRENT_USER_REQUEST:
            raise ValueError("current_user_request has the wrong section kind")
        sections = self.ordered_sections()
        if len({section.section_id for section in sections}) != len(sections):
            raise ValueError("admission section IDs must be unique")
        return self

    def ordered_sections(self) -> tuple[AdmissionSection, ...]:
        return (
            (self.system_instructions,)
            + self.project_instructions
            + self.manifest_context
            + self.conversation_history
            + self.tool_definitions
            + self.tool_results
            + self.additional_runtime_sections
            + (self.current_user_request,)
        )


class AdmissionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    passed: bool
    detail: str


class AdmissionRejection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    explanation: str
    expected: str | int | None = None
    actual: str | int | None = None
    remediation: str


class AdmissionSectionAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: str
    kind: AdmissionSectionKind
    role: str
    token_count: int = Field(ge=0)
    provenance: TokenCountProvenance
    estimator_strategy: str
    estimator_version: int
    normalized_content_hash: str
    manifest_candidate_id: str | None = None


class AdmissionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    admission_id: str
    decision: AdmissionDecision
    admitted: bool
    warning: bool
    policy_id: str
    policy_version: int
    model_identity: ModelIdentity | None = None
    profile_id: str
    manifest_id: str
    manifest_fingerprint: str | None = None
    request_fingerprint: str | None = None
    operational_context_tokens: int = Field(default=0, ge=0)
    maximum_recommended_input_tokens: int = Field(default=0, ge=0)
    actual_estimated_input_tokens: int = Field(default=0, ge=0)
    fixed_reserves: FixedReserves | None = None
    remaining_input_tokens: int = Field(default=0, ge=0)
    requested_output_tokens: int = Field(ge=0)
    requested_tool_result_tokens: int = Field(ge=0)
    sections: tuple[AdmissionSectionAccounting, ...] = ()
    checks: tuple[AdmissionCheck, ...]
    warnings: tuple[str, ...]
    rejections: tuple[AdmissionRejection, ...]
    calculated_at: datetime
    correlation_id: str | None = None


class AdmissionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    admission_id: str
    request_fingerprint: str | None
    manifest_id: str
    manifest_fingerprint: str | None
    profile_id: str
    model_digest: str
    decision: AdmissionDecision
    admitted: bool
    actual_estimated_input_tokens: int = Field(ge=0)
    remaining_input_tokens: int = Field(ge=0)
    reason_codes: tuple[str, ...]
    warnings: tuple[str, ...]
    timestamp: datetime
    dispatch_state: Literal["not_dispatched", "completed", "failed"] = "not_dispatched"
    correlation_id: str | None = None


class AdmittedEnvelope(BaseModel):
    """Internal immutable payload binding the decision to exact outbound sections."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: AdmissionResult
    model_name: str
    sections: tuple[AdmissionSection, ...]
