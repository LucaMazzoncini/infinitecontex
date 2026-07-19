"""G9 immutable linkage between an agent call, context manifest, admission, and transport."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelCallEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    call_id: str = Field(pattern=r"^agent-call-[0-9a-f]{24}$")
    evidence_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    step_number: int = Field(ge=1)
    session_id: str
    task_id: str
    grant_id: str
    profile_id: str
    provider: Literal["ollama"] = "ollama"
    model_name: str
    model_digest: str
    manifest_id: str
    manifest_fingerprint: str
    admission_id: str
    admission_decision: str
    final_prompt_hash: str
    request_envelope_hash: str
    input_bytes: int = Field(ge=0)
    estimated_input_tokens: int = Field(ge=0)
    reserved_output_tokens: int = Field(ge=0)
    operational_context_tokens: int = Field(gt=0)
    remaining_tokens: int = Field(ge=0)
    requested_sampling: dict[str, Any]
    effective_sampling: dict[str, Any]
    transmitted_sampling: dict[str, Any]
    omitted_sampling: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
