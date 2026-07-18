"""Immutable G3 structured mutation contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MUTATION_SCHEMA_VERSION = 1


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileKind(StrEnum):
    SOURCE = "source"
    TEST = "test"
    DOCUMENTATION = "documentation"


class MutationStatus(StrEnum):
    PROPOSAL_VALID = "proposal_valid"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    APPLY_FAILED_ROLLED_BACK = "apply_failed_rolled_back"
    APPLY_FAILED_ROLLBACK_INCOMPLETE = "apply_failed_rollback_incomplete"


class MutationDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ReplaceLineRange(StrictModel):
    operation: Literal["replace_line_range"] = "replace_line_range"
    path: str = Field(min_length=1, max_length=1000)
    file_kind: FileKind
    expected_preimage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    expected_old_text: str = Field(max_length=2 * 1024 * 1024)
    replacement_text: str = Field(max_length=2 * 1024 * 1024)
    expected_postimage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_range(self) -> "ReplaceLineRange":
        if self.end_line < self.start_line:
            raise ValueError("end_line cannot precede start_line")
        return self


class CreateTextFile(StrictModel):
    operation: Literal["create_text_file"] = "create_text_file"
    path: str = Field(min_length=1, max_length=1000)
    file_kind: FileKind
    content: str = Field(max_length=2 * 1024 * 1024)
    expected_postimage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


StructuredMutation = Annotated[Union[ReplaceLineRange, CreateTextFile], Field(discriminator="operation")]


class MutationRequest(StrictModel):
    schema_version: Literal[1] = 1
    operations: tuple[StructuredMutation, ...] = Field(min_length=1, max_length=64)


class FrozenTarget(StrictModel):
    path: str
    file_kind: FileKind
    existed: bool
    preimage_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    postimage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preimage_bytes: int = Field(ge=0)
    postimage_bytes: int = Field(ge=0)


class MutationProposal(StrictModel):
    schema_version: Literal[1] = 1
    proposal_id: str = Field(pattern=r"^mutation-proposal-[0-9a-f]{24}$")
    semantic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str
    plan_revision: int = Field(ge=1)
    graph_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str
    task_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_snapshot_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_id: str
    analysis_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_id: str
    tool_version: str
    tool_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_decision_id: str
    policy_decision_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_capabilities: tuple[str, ...]
    affected_scopes: tuple[str, ...]
    forbidden_scopes: tuple[str, ...]
    operations: tuple[StructuredMutation, ...]
    targets: tuple[FrozenTarget, ...]
    operation_count: int = Field(ge=1)
    total_read_bytes: int = Field(ge=0)
    total_write_bytes: int = Field(ge=0)
    diff_preview: str
    diff_abbreviated: bool = False
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    validation_passed: bool
    approval_required: Literal[True] = True
    created_at: datetime


class MutationApproval(StrictModel):
    schema_version: Literal[1] = 1
    approval_id: str = Field(pattern=r"^mutation-approval-[0-9a-f]{24}$")
    approval_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str
    proposal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str
    plan_revision: int = Field(ge=1)
    repository_snapshot_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor: str = Field(min_length=1, max_length=160)
    decision: MutationDecision
    reason: str | None = Field(default=None, max_length=1000)
    warnings_acknowledged: bool = False
    decided_at: datetime

    @field_validator("actor")
    @classmethod
    def valid_actor(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("approval actor must be printable and non-blank")
        return value


class MutationExecutionRecord(StrictModel):
    schema_version: Literal[1] = 1
    mutation_id: str = Field(pattern=r"^tool-mutation-[0-9a-f]{24}$")
    record_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str
    proposal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_id: str
    approval_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str
    plan_revision: int
    task_id: str
    task_fingerprint: str
    tool_id: str
    tool_fingerprint: str
    repository_snapshot_before: str
    repository_snapshot_after: str | None = None
    target_paths: tuple[str, ...]
    preimage_hashes: tuple[str, ...]
    postimage_hashes: tuple[str, ...]
    operation_count: int
    read_bytes: int
    write_bytes: int
    status: MutationStatus
    rollback_complete: bool | None = None
    warning_codes: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    started_at: datetime
    completed_at: datetime


class MutationValidation(StrictModel):
    valid: bool
    operation_count: int
    target_paths: tuple[str, ...]
    total_write_bytes: int
    errors: tuple[str, ...] = ()


class MutationAuthorization(StrictModel):
    schema_version: Literal[1] = 1
    authorization_id: str = Field(pattern=r"^mutation-authorization-[0-9a-f]{24}$")
    authorization_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str
    proposal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_id: str
    approval_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str
    plan_revision: int
    task_id: str
    task_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_id: str
    tool_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_snapshot_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_paths: tuple[str, ...]
    preimage_hashes: tuple[str, ...]
    postimage_hashes: tuple[str, ...]
    operation_count: int = Field(ge=1, le=64)
    maximum_files: Literal[16] = 16
    maximum_total_bytes: Literal[8388608] = 8388608
    human_cli_only: Literal[True] = True
