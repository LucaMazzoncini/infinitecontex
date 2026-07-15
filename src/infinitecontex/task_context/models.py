"""Immutable contracts for repository resolution and task context fit."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from infinitecontex.context_budget.models import TokenCountProvenance

TASK_CONTEXT_ANALYSIS_SCHEMA_VERSION = 1
REPOSITORY_SNAPSHOT_SCHEMA_VERSION = 1


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RepositoryType(StrEnum):
    GIT = "git"
    FILESYSTEM = "filesystem"


class InventoryClassification(StrEnum):
    TEXT = "text"
    BINARY = "binary"
    TOO_LARGE = "too_large"
    UNSAFE_SYMLINK = "unsafe_symlink"
    INVALID_ENCODING = "invalid_encoding"
    MISSING = "missing"


class InventoryEntry(StrictModel):
    path: str
    size_bytes: int = Field(ge=0)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    classification: InventoryClassification
    tracked: bool
    staged: bool = False
    modified: bool = False
    untracked: bool = False
    symlink: bool = False


class RepositorySnapshot(StrictModel):
    schema_version: Literal[1] = 1
    snapshot_id: str = Field(pattern=r"^repository-snapshot-[0-9a-f]{24}$")
    repository_root_reference: str
    repository_identity: str
    repository_type: RepositoryType
    git_available: bool
    commit_hash: str | None = None
    branch: str | None = None
    dirty: bool
    staged_paths: tuple[str, ...]
    modified_paths: tuple[str, ...]
    tracked_file_state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    permitted_untracked_state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ignore_policy_version: int = Field(ge=1)
    path_case_policy: Literal["sensitive", "insensitive"]
    symlink_policy: str
    inventory_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_count: int = Field(ge=0)
    tracked_file_count: int = Field(ge=0)
    permitted_untracked_file_count: int = Field(ge=0)
    created_at: datetime
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_timezone(self) -> "RepositorySnapshot":
        if self.created_at.tzinfo is None:
            raise ValueError("repository snapshot timestamp must include a timezone")
        return self


class RepositoryInventory(StrictModel):
    snapshot: RepositorySnapshot
    entries: tuple[InventoryEntry, ...]
    ignored_paths: tuple[str, ...] = ()


class ReferenceRequirement(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"


class PathReferenceKind(StrEnum):
    EXACT_FILE = "exact_file"
    EXACT_DIRECTORY = "exact_directory"
    GLOB = "glob"
    TEST_FILE = "test_file"
    DOCUMENTATION_FILE = "documentation_file"
    CONFIGURATION_FILE = "configuration_file"
    SOURCE_RANGE = "source_range"
    LOGICAL_SCOPE = "logical_scope"


class PathReference(StrictModel):
    original_value: str = Field(min_length=1, max_length=1000)
    normalized_value: str | None = Field(default=None, max_length=1000)
    kind: PathReferenceKind = PathReferenceKind.EXACT_FILE
    requirement: ReferenceRequirement = ReferenceRequirement.REQUIRED
    source_task_id: str
    source_field: str
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    expected_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_snapshot_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    language_hint: str | None = None
    logical_label: str | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "PathReference":
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("source ranges require line_start and line_end")
        if self.line_start is not None and self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end cannot precede line_start")
        if self.kind == PathReferenceKind.SOURCE_RANGE and self.line_start is None:
            raise ValueError("source_range references require a line range")
        return self


class PathResolutionOutcome(StrEnum):
    RESOLVED = "resolved"
    RESOLVED_MULTIPLE = "resolved_multiple"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    FORBIDDEN = "forbidden"
    IGNORED = "ignored"
    BINARY = "binary"
    TOO_LARGE = "too_large"
    UNSAFE_PATH = "unsafe_path"
    UNSAFE_SYMLINK = "unsafe_symlink"
    INVALID_RANGE = "invalid_range"
    HASH_MISMATCH = "hash_mismatch"
    STALE_SNAPSHOT = "stale_snapshot"
    UNSUPPORTED = "unsupported"
    INVALID_REFERENCE = "invalid_reference"


class ResolvedPathMatch(StrictModel):
    path: str
    line_start: int | None = None
    line_end: int | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class PathResolution(StrictModel):
    reference: PathReference
    normalized_value: str | None
    outcome: PathResolutionOutcome
    matches: tuple[ResolvedPathMatch, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class SymbolKind(StrEnum):
    MODULE = "module"
    NAMESPACE = "namespace"
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    FUNCTION = "function"
    METHOD = "method"
    PROPERTY = "property"
    FIELD = "field"
    CONSTANT = "constant"
    TEST = "test"
    UNKNOWN = "unknown"


class SymbolReference(StrictModel):
    original_reference: str = Field(min_length=1, max_length=1000)
    language: str
    symbol_name: str = Field(min_length=1, max_length=500)
    qualified_name: str | None = None
    file_hint: str | None = None
    module_or_namespace: str | None = None
    symbol_kind: SymbolKind = SymbolKind.UNKNOWN
    signature_hint: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    requirement: ReferenceRequirement = ReferenceRequirement.REQUIRED
    source_task_id: str
    source_field: str
    expected_symbol_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class SymbolResolutionOutcome(StrEnum):
    RESOLVED_EXACT = "resolved_exact"
    RESOLVED_WITH_FILE_HINT = "resolved_with_file_hint"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    RESOLVER_UNAVAILABLE = "resolver_unavailable"
    SYNTAX_ERROR = "syntax_error"
    INVALID_REFERENCE = "invalid_reference"
    STALE_CONTENT = "stale_content"
    HASH_MISMATCH = "hash_mismatch"
    UNSAFE_SOURCE = "unsafe_source"
    TOO_LARGE = "too_large"


class ResolvedSymbolMatch(StrictModel):
    path: str
    qualified_name: str
    symbol_kind: SymbolKind
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    normalized_source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolver_id: str
    resolver_version: int = Field(ge=1)
    decorators: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class SymbolResolution(StrictModel):
    reference: SymbolReference
    outcome: SymbolResolutionOutcome
    matches: tuple[ResolvedSymbolMatch, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class ScopeConflictKind(StrEnum):
    EXACT_OVERLAP = "exact_overlap"
    AFFECTED_UNDER_FORBIDDEN = "affected_under_forbidden"
    REQUIRED_FILE_FORBIDDEN = "required_file_forbidden"
    DUPLICATE_SCOPE = "duplicate_scope"
    REDUNDANT_SCOPE = "redundant_scope"
    CASE_CONFLICT = "case_conflict"
    OUTPUT_ESCAPE = "output_escape"
    ROOT_WIDE_AFFECTED = "root_wide_affected"


class ScopeConflict(StrictModel):
    kind: ScopeConflictKind
    left: str
    right: str | None = None
    blocking: bool
    message: str


class TaskResolutionReport(StrictModel):
    schema_version: Literal[1] = 1
    plan_id: str
    plan_revision: int
    task_id: str
    task_fingerprint: str
    repository_snapshot: RepositorySnapshot
    path_resolutions: tuple[PathResolution, ...]
    symbol_resolutions: tuple[SymbolResolution, ...]
    scope_conflicts: tuple[ScopeConflict, ...]
    warnings: tuple[str, ...]


class TaskContextDecision(StrEnum):
    FITS_TARGET = "fits_target"
    FITS_WITH_WARNING = "fits_with_warning"
    FITS_HARD_LIMIT = "fits_hard_limit"
    SPLIT_REQUIRED = "split_required"
    REQUIRED_REFERENCE_UNRESOLVED = "required_reference_unresolved"
    SCOPE_CONFLICT = "scope_conflict"
    UNSUPPORTED_REQUIRED_SYMBOL = "unsupported_required_symbol"
    PROFILE_UNAVAILABLE = "profile_unavailable"
    PROFILE_MISMATCH = "profile_mismatch"
    STALE_REPOSITORY_ANALYSIS = "stale_repository_analysis"
    INVALID_TASK = "invalid_task"
    INVALID_PLAN = "invalid_plan"
    INVALID_REQUEST = "invalid_request"


class AnalysisCandidateRecord(StrictModel):
    candidate_id: str
    candidate_fingerprint: str
    category: str
    token_count: int = Field(ge=0)
    provenance: TokenCountProvenance | None = None
    source_path: str | None = None
    source_range: str | None = None
    symbol_id: str | None = None
    content_hash: str | None = None
    reason: str


class RemediationAction(StrictModel):
    code: str
    message: str
    reference: str | None = None


class TaskContextAnalysis(StrictModel):
    schema_version: Literal[1] = 1
    analysis_id: str = Field(pattern=r"^task-context-[0-9a-f]{24}$")
    semantic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str
    plan_revision: int = Field(ge=1)
    graph_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str
    task_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_snapshot_id: str
    repository_snapshot_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str
    provider: str
    normalized_model_name: str
    model_digest: str
    operational_context_tokens: int = Field(gt=0)
    maximum_recommended_input_tokens: int = Field(gt=0)
    estimator_strategy: str
    estimator_version: int = Field(ge=1)
    resolution_policy_id: str
    resolution_policy_version: int = Field(ge=1)
    ranking_policy_id: str
    ranking_policy_version: int = Field(ge=1)
    packing_strategy_id: str
    packing_strategy_version: int = Field(ge=1)
    context_manifest_id: str | None
    context_manifest_fingerprint: str | None
    decision: TaskContextDecision
    passing: bool
    mandatory_token_total: int = Field(ge=0)
    optional_token_total: int = Field(ge=0)
    packed_token_total: int = Field(ge=0)
    remaining_input_tokens: int = Field(ge=0)
    token_deficit: int = Field(ge=0)
    resolved_reference_count: int = Field(ge=0)
    unresolved_reference_count: int = Field(ge=0)
    scope_conflict_count: int = Field(ge=0)
    path_resolutions: tuple[PathResolution, ...]
    symbol_resolutions: tuple[SymbolResolution, ...]
    scope_conflicts: tuple[ScopeConflict, ...]
    included_candidates: tuple[AnalysisCandidateRecord, ...]
    excluded_candidates: tuple[AnalysisCandidateRecord, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    remediation_actions: tuple[RemediationAction, ...]
    created_at: datetime

    @model_validator(mode="after")
    def validate_analysis(self) -> "TaskContextAnalysis":
        if self.created_at.tzinfo is None:
            raise ValueError("analysis timestamp must include a timezone")
        passing_decision = self.decision in {
            TaskContextDecision.FITS_TARGET,
            TaskContextDecision.FITS_WITH_WARNING,
            TaskContextDecision.FITS_HARD_LIMIT,
        }
        if self.passing != passing_decision:
            raise ValueError("passing flag is inconsistent with the fit decision")
        return self


class TaskContextCurrentPointer(StrictModel):
    schema_version: Literal[1] = 1
    plan_id: str
    plan_revision: int = Field(ge=1)
    task_id: str
    analysis_id: str
    semantic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_at: datetime


class AnalysisStaleness(StrictModel):
    stale: bool
    reasons: tuple[str, ...]
