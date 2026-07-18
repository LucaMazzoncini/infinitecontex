"""Data-bound read-only handlers; inputs are admitted before these functions run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from infinitecontex.task_context.models import (
    InventoryClassification,
    PathReference,
    PathReferenceKind,
    PathResolutionOutcome,
    RepositoryInventory,
)
from infinitecontex.task_context.paths import PathResolver, repository_glob_match
from infinitecontex.tools.execution_models import (
    ExecutionStatus,
    FileMetadata,
    InvocationInput,
    OperationKind,
    RepositoryToolResult,
    SearchMatch,
)


@dataclass(frozen=True)
class ReadHandlerContext:
    repository_root: Path
    inventory: RepositoryInventory


class ReadHandler(Protocol):
    def __call__(self, inputs: InvocationInput, context: ReadHandlerContext) -> RepositoryToolResult: ...


@dataclass(frozen=True)
class HandlerBinding:
    tool_id: str
    tool_version: str
    definition_fingerprint: str
    handler_name: str
    handler: ReadHandler


class ReadHandlerRegistry:
    def __init__(self, bindings: tuple[HandlerBinding, ...]) -> None:
        self._bindings = {item.tool_id: item for item in bindings}
        if len(self._bindings) != len(bindings):
            raise ValueError("duplicate read handler tool ID")

    def get(self, tool_id: str) -> HandlerBinding | None:
        return self._bindings.get(tool_id)

    def list(self) -> tuple[HandlerBinding, ...]:
        return tuple(sorted(self._bindings.values(), key=lambda item: item.tool_id))


def list_files(inputs: InvocationInput, context: ReadHandlerContext) -> RepositoryToolResult:
    resolver = PathResolver(context.repository_root, context.inventory)
    pattern = _normalized_optional(resolver, inputs.glob, PathReferenceKind.GLOB)
    prefix = _normalized_optional(resolver, inputs.directory_prefix, PathReferenceKind.EXACT_DIRECTORY)
    suffix = inputs.suffix.casefold() if inputs.suffix else None
    eligible = tuple(
        sorted(
            (
                entry
                for entry in context.inventory.entries
                if (pattern is None or repository_glob_match(entry.path, pattern))
                and (prefix is None or entry.path == prefix or entry.path.startswith(prefix.rstrip("/") + "/"))
                and (suffix is None or entry.path.casefold().endswith(suffix))
            ),
            key=lambda item: (item.path.casefold(), item.path),
        )
    )
    selected = eligible[inputs.offset : inputs.offset + inputs.limit]
    has_more = inputs.offset + len(selected) < len(eligible)
    files = tuple(_metadata(item) for item in selected)
    return RepositoryToolResult(
        status=ExecutionStatus.PARTIAL if has_more else ExecutionStatus.SUCCESS,
        operation=inputs.operation,
        repository_snapshot_fingerprint=context.inventory.snapshot.semantic_fingerprint,
        has_more=has_more,
        continuation_offset=inputs.offset + len(selected) if has_more else None,
        files=files,
        message=f"Returned {len(files)} inventoried repository files.",
    )


def search_paths(inputs: InvocationInput, context: ReadHandlerContext) -> RepositoryToolResult:
    return list_files(inputs, context)


def read_file(inputs: InvocationInput, context: ReadHandlerContext) -> RepositoryToolResult:
    assert inputs.path is not None
    resolver = PathResolver(context.repository_root, context.inventory)
    reference = PathReference(
        original_value=inputs.path,
        kind=PathReferenceKind.EXACT_FILE,
        source_task_id=inputs.path,
        source_field="g2-read",
        expected_snapshot_fingerprint=context.inventory.snapshot.semantic_fingerprint,
    )
    frozen = resolver.resolve(reference)
    if not frozen.sources:
        return _resolution_failure(inputs.operation, context.inventory, frozen.resolution.outcome)
    source = frozen.sources[0]
    encoding: Literal["utf-8", "utf-8-sig"] = "utf-8-sig" if source.content.startswith("\ufeff") else "utf-8"
    text = source.content.removeprefix("\ufeff")
    lines = text.splitlines(keepends=True)
    line_count = len(lines)
    start = inputs.start_line or (inputs.offset + 1)
    requested_end = inputs.end_line or (start + inputs.limit - 1)
    if start > max(line_count, 1):
        return RepositoryToolResult(
            status=ExecutionStatus.INVALID_REQUEST,
            operation=inputs.operation,
            repository_snapshot_fingerprint=context.inventory.snapshot.semantic_fingerprint,
            normalized_path=source.path,
            content_hash=source.full_content_hash,
            encoding=encoding,
            line_count=line_count,
            byte_count=source.size_bytes,
            requested_range=(start, requested_end),
            error_codes=("range_out_of_bounds",),
            message=f"Requested line {start} exceeds the {line_count}-line file.",
        )
    returned_end = min(requested_end, line_count)
    content = "" if line_count == 0 else "".join(lines[start - 1 : returned_end])
    has_more = returned_end < line_count
    next_range = (returned_end + 1, min(returned_end + inputs.limit, line_count)) if has_more else None
    return RepositoryToolResult(
        status=ExecutionStatus.PARTIAL if has_more else ExecutionStatus.SUCCESS,
        operation=inputs.operation,
        repository_snapshot_fingerprint=context.inventory.snapshot.semantic_fingerprint,
        normalized_path=source.path,
        content_hash=source.full_content_hash,
        encoding=encoding,
        line_count=line_count,
        byte_count=source.size_bytes,
        requested_range=(start, requested_end),
        returned_range=(start, returned_end) if line_count else None,
        has_more=has_more,
        continuation_offset=returned_end if has_more else None,
        next_range=next_range,
        content=content,
        message=f"Returned lines {start}-{returned_end} of {line_count} from {source.path}.",
    )


def search_literal(inputs: InvocationInput, context: ReadHandlerContext) -> RepositoryToolResult:
    assert inputs.query is not None
    resolver = PathResolver(context.repository_root, context.inventory)
    pattern = _normalized_optional(resolver, inputs.glob, PathReferenceKind.GLOB)
    candidates = tuple(
        sorted(
            (
                entry
                for entry in context.inventory.entries
                if entry.classification == InventoryClassification.TEXT
                and (pattern is None or repository_glob_match(entry.path, pattern))
            ),
            key=lambda item: (item.path.casefold(), item.path),
        )
    )
    matches: list[SearchMatch] = []
    files_scanned = 0
    bytes_scanned = 0
    limited = False
    for entry in candidates:
        if files_scanned >= inputs.maximum_files or bytes_scanned + entry.size_bytes > inputs.maximum_bytes:
            limited = True
            break
        frozen = resolver.resolve(
            PathReference(
                original_value=entry.path,
                kind=PathReferenceKind.EXACT_FILE,
                source_task_id=entry.path,
                source_field="g2-literal-search",
                expected_snapshot_fingerprint=context.inventory.snapshot.semantic_fingerprint,
            )
        )
        if not frozen.sources:
            return _resolution_failure(inputs.operation, context.inventory, frozen.resolution.outcome)
        source = frozen.sources[0]
        text = source.content.removeprefix("\ufeff")
        files_scanned += 1
        bytes_scanned += source.size_bytes
        lines = text.splitlines()
        for line_index, line in enumerate(lines):
            for column in _literal_columns(line, inputs.query, inputs.ignore_case, inputs.whole_word):
                first = max(0, line_index - inputs.context_lines)
                last = min(len(lines), line_index + inputs.context_lines + 1)
                matches.append(
                    SearchMatch(
                        path=source.path,
                        line=line_index + 1,
                        column=column + 1,
                        snippet="\n".join(lines[first:last])[:2000],
                        content_hash=source.full_content_hash,
                    )
                )
                if len(matches) >= inputs.offset + inputs.maximum_matches + 1:
                    limited = True
                    break
            if limited and len(matches) >= inputs.offset + inputs.maximum_matches + 1:
                break
        if limited and len(matches) >= inputs.offset + inputs.maximum_matches + 1:
            break
    selected = tuple(matches[inputs.offset : inputs.offset + inputs.maximum_matches])
    has_more = limited or inputs.offset + len(selected) < len(matches)
    status = (
        ExecutionStatus.PARTIAL if has_more else ExecutionStatus.NO_MATCHES if not selected else ExecutionStatus.SUCCESS
    )
    return RepositoryToolResult(
        status=status,
        operation=inputs.operation,
        repository_snapshot_fingerprint=context.inventory.snapshot.semantic_fingerprint,
        has_more=has_more,
        continuation_offset=inputs.offset + len(selected) if has_more else None,
        matches=selected,
        files_scanned=files_scanned,
        bytes_scanned=bytes_scanned,
        warnings=("bounded_search_limit_reached",) if has_more else (),
        message=f"Returned {len(selected)} literal matches from {files_scanned} files.",
    )


def _normalized_optional(resolver: PathResolver, value: str | None, kind: PathReferenceKind) -> str | None:
    if value is None:
        return None
    normalized, error = resolver.normalize(value, kind)
    if error or normalized is None:
        raise ValueError(error or "invalid repository path filter")
    return normalized


def _metadata(entry: object) -> FileMetadata:
    from infinitecontex.task_context.models import InventoryEntry

    assert isinstance(entry, InventoryEntry)
    return FileMetadata(
        path=entry.path,
        size_bytes=entry.size_bytes,
        content_hash=entry.content_hash,
        classification=entry.classification.value,
        tracked=entry.tracked,
        modified=entry.modified,
        untracked=entry.untracked,
    )


def _resolution_failure(
    operation: OperationKind, inventory: RepositoryInventory, outcome: PathResolutionOutcome
) -> RepositoryToolResult:
    mapping = {
        PathResolutionOutcome.MISSING: ExecutionStatus.MISSING_PATH,
        PathResolutionOutcome.BINARY: ExecutionStatus.BINARY_FILE,
        PathResolutionOutcome.TOO_LARGE: ExecutionStatus.FILE_TOO_LARGE,
        PathResolutionOutcome.UNSUPPORTED: ExecutionStatus.UNSUPPORTED_ENCODING,
        PathResolutionOutcome.STALE_SNAPSHOT: ExecutionStatus.STALE_SNAPSHOT,
        PathResolutionOutcome.UNSAFE_SYMLINK: ExecutionStatus.FORBIDDEN_PATH,
        PathResolutionOutcome.UNSAFE_PATH: ExecutionStatus.FORBIDDEN_PATH,
        PathResolutionOutcome.IGNORED: ExecutionStatus.FORBIDDEN_PATH,
    }
    return RepositoryToolResult(
        status=mapping.get(outcome, ExecutionStatus.INVALID_REQUEST),
        operation=operation,
        repository_snapshot_fingerprint=inventory.snapshot.semantic_fingerprint,
        error_codes=(outcome.value,),
        message=f"Repository path resolution failed: {outcome.value}.",
    )


def _literal_columns(line: str, query: str, ignore_case: bool, whole_word: bool) -> tuple[int, ...]:
    haystack = line.casefold() if ignore_case else line
    needle = query.casefold() if ignore_case else query
    values: list[int] = []
    offset = 0
    while True:
        found = haystack.find(needle, offset)
        if found < 0:
            return tuple(values)
        end = found + len(needle)
        before_ok = found == 0 or not _word_character(haystack[found - 1])
        after_ok = end == len(haystack) or not _word_character(haystack[end])
        if not whole_word or (before_ok and after_ok):
            values.append(found)
        offset = found + max(1, len(needle))


def _word_character(value: str) -> bool:
    return value.isalnum() or value == "_"
