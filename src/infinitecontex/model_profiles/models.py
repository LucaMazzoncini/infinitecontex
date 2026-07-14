"""Versioned contracts for model identity and conservative token budgets."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_PROFILE_SCHEMA_VERSION = 1


class IdentityStrength(StrEnum):
    VERIFIED = "verified"
    WEAK = "weak"


class ValueProvenance(StrEnum):
    DETECTED = "detected"
    CONFIGURED = "configured"
    ESTIMATED = "estimated"
    CALIBRATED = "calibrated"


class CalibrationStatus(StrEnum):
    UNCALIBRATED = "uncalibrated"
    CALIBRATED = "calibrated"
    STALE = "stale"


def normalize_model_name(name: str) -> str:
    normalized = name.strip().casefold()
    if not normalized:
        raise ValueError("model name cannot be empty")
    return normalized


class ModelIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    normalized_model_name: str = Field(min_length=1)
    model_digest: str | None = None
    identity_strength: IdentityStrength
    model_family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    inspected_at: datetime

    @model_validator(mode="after")
    def validate_identity(self) -> "ModelIdentity":
        if self.normalized_model_name != normalize_model_name(self.model_name):
            raise ValueError("normalized_model_name does not match model_name")
        digest = self.model_digest.strip() if self.model_digest else None
        self.model_digest = digest
        expected = IdentityStrength.VERIFIED if digest else IdentityStrength.WEAK
        if self.identity_strength != expected:
            raise ValueError("identity_strength must be verified only when a digest is present")
        if self.inspected_at.tzinfo is None:
            raise ValueError("inspected_at must include a timezone")
        return self


class CalibrationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    reference: str
    summary: str = ""


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    profile_id: str = Field(min_length=1)
    model_identity: ModelIdentity
    advertised_context_tokens: int | None = Field(default=None, gt=0)
    configured_context_tokens: int = Field(gt=0)
    operational_context_tokens: int = Field(gt=0)
    reserved_output_tokens: int = Field(ge=0)
    reserved_tool_result_tokens: int = Field(ge=0)
    reserved_system_prompt_tokens: int = Field(ge=0)
    safety_margin_tokens: int = Field(ge=0)
    maximum_recommended_input_tokens: int = Field(gt=0)
    tokenizer_strategy: str = Field(min_length=1)
    token_estimation_strategy: str = Field(min_length=1)
    calibration_status: CalibrationStatus
    calibrated_at: datetime | None = None
    calibration_evidence: list[CalibrationEvidence] = Field(default_factory=list)
    hardware_profile_ref: str | None = None
    created_at: datetime
    updated_at: datetime
    provenance: dict[str, ValueProvenance]

    @model_validator(mode="after")
    def validate_budget_and_state(self) -> "ModelProfile":
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("profile timestamps must include a timezone")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.operational_context_tokens > self.configured_context_tokens:
            raise ValueError("operational context cannot exceed configured Ollama context")
        if (
            self.advertised_context_tokens is not None
            and self.configured_context_tokens > self.advertised_context_tokens
        ):
            raise ValueError("configured context cannot exceed advertised capacity without an explicit override")
        reserves = (
            self.reserved_output_tokens
            + self.reserved_tool_result_tokens
            + self.reserved_system_prompt_tokens
            + self.safety_margin_tokens
        )
        expected_input = self.operational_context_tokens - reserves
        if expected_input <= 0:
            raise ValueError("token reserves must leave a positive input budget")
        if self.maximum_recommended_input_tokens != expected_input:
            raise ValueError("maximum recommended input must equal operational context minus all reserves")
        if self.calibration_status == CalibrationStatus.CALIBRATED:
            if self.calibrated_at is None or not self.calibration_evidence:
                raise ValueError("calibrated profiles require a calibration date and evidence")
        elif self.calibrated_at is not None:
            raise ValueError("uncalibrated or stale profiles cannot have a calibration date")
        required_provenance = {
            "advertised_context_tokens",
            "configured_context_tokens",
            "operational_context_tokens",
            "reserved_output_tokens",
            "reserved_tool_result_tokens",
            "reserved_system_prompt_tokens",
            "safety_margin_tokens",
            "maximum_recommended_input_tokens",
            "tokenizer_strategy",
            "token_estimation_strategy",
        }
        if not required_provenance.issubset(self.provenance):
            raise ValueError("profile is missing required value provenance")
        return self
