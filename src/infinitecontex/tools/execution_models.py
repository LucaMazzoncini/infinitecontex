"""Immutable G2 invocation, grant, result, and execution-record contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EXECUTION_SCHEMA_VERSION = 1
EXECUTION_POLICY_VERSION = 1


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CallerType(StrEnum):
    HUMAN_CLI = "human_cli"
    TASK_BOUND = "task_bound_internal"
    FUTURE_AGENT = "future_agent"


class AuthorizationSource(StrEnum):
    EXPLICIT_HUMAN_CLI = "explicit_human_cli"
    TASK_POLICY = "task_policy"


class OperationKind(StrEnum):
    LIST_FILES = "list_files"
    READ_FILE = "read_file"
    READ_RANGE = "read_range"
    SEARCH_PATHS = "search_paths"
    SEARCH_LITERAL = "search_literal"


class ExecutionStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial_success"
    NO_MATCHES = "no_matches"
    REJECTED = "rejected_invocation"
    STALE_SNAPSHOT = "stale_snapshot"
    MISSING_PATH = "missing_path"
    FORBIDDEN_PATH = "forbidden_path"
    SENSITIVE_PATH = "sensitive_path"
    BINARY_FILE = "binary_file"
    UNSUPPORTED_ENCODING = "unsupported_encoding"
    FILE_TOO_LARGE = "file_too_large"
    RESULT_LIMIT = "result_limit_reached"
    INVALID_REQUEST = "invalid_request"
    INTERNAL_FAILURE = "internal_handler_failure"


class InvocationInput(StrictModel):
    operation: OperationKind
    path: str | None = Field(default=None, max_length=1000)
    glob: str | None = Field(default=None, max_length=1000)
    suffix: str | None = Field(default=None, max_length=100)
    directory_prefix: str | None = Field(default=None, max_length=1000)
    query: str | None = Field(default=None, min_length=1, max_length=1000)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    ignore_case: bool = False
    whole_word: bool = False
    context_lines: int = Field(default=0, ge=0, le=10)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=1000)
    maximum_matches: int = Field(default=100, ge=1, le=10_000)
    maximum_files: int = Field(default=1000, ge=1, le=10_000)
    maximum_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=64 * 1024 * 1024)

    @model_validator(mode="after")
    def validate_operation(self) -> "InvocationInput":
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("line ranges require both start_line and end_line")
        if self.start_line is not None and self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line cannot precede start_line")
        if self.operation in {OperationKind.READ_FILE, OperationKind.READ_RANGE} and self.path is None:
            raise ValueError("file reads require an explicit path")
        if self.operation == OperationKind.READ_RANGE and self.start_line is None:
            raise ValueError("range reads require start_line and end_line")
        if self.operation == OperationKind.SEARCH_LITERAL and self.query is None:
            raise ValueError("literal search requires a query")
        return self


class ToolInvocation(StrictModel):
    schema_version: Literal[1] = 1
    invocation_id: str = Field(pattern=r"^tool-invocation-[0-9a-f]{24}$")
    invocation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    correlation_id: str = Field(min_length=1, max_length=200)
    tool_id: str = Field(pattern=r"^tool-[0-9a-f]{24}$")
    tool_version: str
    registry_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_snapshot_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str | None = None
    plan_revision: int | None = Field(default=None, ge=1)
    task_id: str | None = None
    task_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    normalized_input: InvocationInput
    requested_scopes: tuple[str, ...]
    caller_type: CallerType
    authorization_source: AuthorizationSource
    created_at: datetime


class ReadOnlyExecutionGrant(StrictModel):
    schema_version: Literal[1] = 1
    grant_id: str = Field(pattern=r"^read-grant-[0-9a-f]{24}$")
    grant_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    invocation_id: str
    invocation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_id: str
    tool_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_snapshot_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorized_scopes: tuple[str, ...]
    authorization_source: AuthorizationSource
    read_repository_only: Literal[True] = True
    created_at: datetime


class FileMetadata(StrictModel):
    path: str
    size_bytes: int = Field(ge=0)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    classification: str
    tracked: bool
    modified: bool
    untracked: bool


class SearchMatch(StrictModel):
    path: str
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    snippet: str = Field(max_length=2000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RepositoryToolResult(StrictModel):
    schema_version: Literal[1] = 1
    status: ExecutionStatus
    operation: OperationKind
    repository_snapshot_fingerprint: str
    normalized_path: str | None = None
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    encoding: Literal["utf-8", "utf-8-sig"] | None = None
    line_count: int | None = Field(default=None, ge=0)
    byte_count: int = Field(default=0, ge=0)
    requested_range: tuple[int, int] | None = None
    returned_range: tuple[int, int] | None = None
    has_more: bool = False
    continuation_offset: int | None = Field(default=None, ge=0)
    next_range: tuple[int, int] | None = None
    content: str | None = None
    files: tuple[FileMetadata, ...] = ()
    matches: tuple[SearchMatch, ...] = ()
    files_scanned: int = Field(default=0, ge=0)
    bytes_scanned: int = Field(default=0, ge=0)
    warnings: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    message: str


class ToolExecutionRecord(StrictModel):
    schema_version: Literal[1] = 1
    execution_id: str = Field(pattern=r"^tool-execution-[0-9a-f]{24}$")
    record_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    invocation_id: str
    invocation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    correlation_id: str
    tool_id: str
    tool_version: str
    tool_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_snapshot_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str | None = None
    plan_revision: int | None = None
    task_id: str | None = None
    task_fingerprint: str | None = None
    authorization_source: AuthorizationSource
    normalized_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: str
    result_status: ExecutionStatus
    result_count: int = Field(ge=0)
    byte_count: int = Field(ge=0)
    files_scanned: int = Field(ge=0)
    bytes_scanned: int = Field(ge=0)
    content_hashes: tuple[str, ...]
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]
    content_returned: bool
    started_at: datetime
    completed_at: datetime


class ExecutionEnvelope(StrictModel):
    invocation: ToolInvocation
    grant: ReadOnlyExecutionGrant | None
    result: RepositoryToolResult
    record: ToolExecutionRecord
