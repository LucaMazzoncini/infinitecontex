"""Cross-platform repository-relative path normalization and freezing."""

from __future__ import annotations

import fnmatch
import hashlib
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from infinitecontex.context_budget.estimation import normalize_text
from infinitecontex.task_context.models import (
    InventoryClassification,
    InventoryEntry,
    PathReference,
    PathReferenceKind,
    PathResolution,
    PathResolutionOutcome,
    ReferenceRequirement,
    RepositoryInventory,
    ResolvedPathMatch,
)
from infinitecontex.task_context.policy import RepositoryResolutionPolicy

_WILDCARDS = frozenset("*?[")


@dataclass(frozen=True)
class FrozenSource:
    path: str
    content: str
    full_content_hash: str
    frozen_content_hash: str
    line_start: int | None
    line_end: int | None
    size_bytes: int


@dataclass(frozen=True)
class FrozenPathResolution:
    resolution: PathResolution
    sources: tuple[FrozenSource, ...] = ()


class _StaleSourceError(ValueError):
    pass


class PathResolver:
    def __init__(
        self,
        repository_root: Path,
        inventory: RepositoryInventory,
        policy: RepositoryResolutionPolicy | None = None,
    ) -> None:
        self.root = repository_root.resolve(strict=True)
        self.inventory = inventory
        self.policy = policy or RepositoryResolutionPolicy()
        self.by_path = {entry.path: entry for entry in inventory.entries}
        self.by_case: dict[str, list[InventoryEntry]] = {}
        for entry in inventory.entries:
            self.by_case.setdefault(entry.path.casefold(), []).append(entry)

    def resolve(self, reference: PathReference) -> FrozenPathResolution:
        normalized, error = self.normalize(reference.original_value, reference.kind)
        updated = reference.model_copy(update={"normalized_value": normalized})
        if error:
            return FrozenPathResolution(
                PathResolution(
                    reference=updated,
                    normalized_value=normalized,
                    outcome=PathResolutionOutcome.UNSAFE_PATH,
                    errors=(error,),
                )
            )
        assert normalized is not None
        if reference.expected_snapshot_fingerprint not in {None, self.inventory.snapshot.semantic_fingerprint}:
            return FrozenPathResolution(
                PathResolution(
                    reference=updated,
                    normalized_value=normalized,
                    outcome=PathResolutionOutcome.STALE_SNAPSHOT,
                    errors=("Reference expects a different repository snapshot.",),
                )
            )
        if self._is_excluded(normalized):
            return FrozenPathResolution(
                PathResolution(
                    reference=updated,
                    normalized_value=normalized,
                    outcome=PathResolutionOutcome.IGNORED,
                    errors=("Path is excluded by the repository resolution policy.",),
                )
            )
        matches, ambiguous = self._matches(normalized, reference.kind)
        if ambiguous:
            return FrozenPathResolution(
                PathResolution(
                    reference=updated,
                    normalized_value=normalized,
                    outcome=PathResolutionOutcome.AMBIGUOUS,
                    errors=("Path differs only by case from multiple inventory entries.",),
                )
            )
        if len(matches) > self.policy.maximum_glob_matches:
            return FrozenPathResolution(
                PathResolution(
                    reference=updated,
                    normalized_value=normalized,
                    outcome=PathResolutionOutcome.INVALID_REFERENCE,
                    errors=(
                        f"Reference matched {len(matches)} files; the safe limit is "
                        f"{self.policy.maximum_glob_matches}.",
                    ),
                )
            )
        if not matches:
            if self._ignored_matches(normalized, reference.kind):
                return FrozenPathResolution(
                    PathResolution(
                        reference=updated,
                        normalized_value=normalized,
                        outcome=PathResolutionOutcome.IGNORED,
                        errors=("Path is ignored by the repository's Git ignore rules.",),
                    )
                )
            return FrozenPathResolution(
                PathResolution(
                    reference=updated,
                    normalized_value=normalized,
                    outcome=PathResolutionOutcome.MISSING,
                    errors=("No inventory entry matches this reference.",),
                )
            )
        if reference.requirement == ReferenceRequirement.FORBIDDEN:
            forbidden_records = tuple(
                ResolvedPathMatch(
                    path=entry.path,
                    content_hash=entry.content_hash or "0" * 64,
                    frozen_content_hash=entry.content_hash or "0" * 64,
                    size_bytes=entry.size_bytes,
                    fingerprint=_sha256(f"{entry.path}\0forbidden\0{entry.content_hash or ''}"),
                )
                for entry in matches
            )
            return FrozenPathResolution(
                PathResolution(
                    reference=updated,
                    normalized_value=normalized,
                    outcome=PathResolutionOutcome.FORBIDDEN,
                    matches=forbidden_records,
                    warnings=("Forbidden references are resolved as constraints and never included as context.",),
                )
            )
        blocked = self._blocked_outcome(matches)
        if blocked is not None:
            return FrozenPathResolution(
                PathResolution(
                    reference=updated,
                    normalized_value=normalized,
                    outcome=blocked,
                    errors=(f"Matched source is classified as {blocked.value}.",),
                )
            )
        sources: list[FrozenSource] = []
        records: list[ResolvedPathMatch] = []
        total = 0
        for entry in matches:
            try:
                source = self._freeze(entry, reference)
            except _StaleSourceError as exc:
                return FrozenPathResolution(
                    PathResolution(
                        reference=updated,
                        normalized_value=normalized,
                        outcome=PathResolutionOutcome.STALE_SNAPSHOT,
                        errors=(str(exc),),
                    )
                )
            except (OSError, UnicodeError, ValueError) as exc:
                return FrozenPathResolution(
                    PathResolution(
                        reference=updated,
                        normalized_value=normalized,
                        outcome=PathResolutionOutcome.INVALID_RANGE,
                        errors=(str(exc),),
                    )
                )
            total += entry.size_bytes
            if total > self.policy.maximum_total_source_bytes:
                return FrozenPathResolution(
                    PathResolution(
                        reference=updated,
                        normalized_value=normalized,
                        outcome=PathResolutionOutcome.TOO_LARGE,
                        errors=("Resolved sources exceed the total frozen-source byte limit.",),
                    )
                )
            if reference.expected_content_hash and reference.expected_content_hash != source.full_content_hash:
                return FrozenPathResolution(
                    PathResolution(
                        reference=updated,
                        normalized_value=normalized,
                        outcome=PathResolutionOutcome.HASH_MISMATCH,
                        errors=("Expected content hash does not match the current inventory.",),
                    )
                )
            fingerprint = _sha256(
                f"{source.path}\0{source.line_start}\0{source.line_end}\0{source.frozen_content_hash}"
            )
            records.append(
                ResolvedPathMatch(
                    path=source.path,
                    line_start=source.line_start,
                    line_end=source.line_end,
                    content_hash=source.full_content_hash,
                    frozen_content_hash=source.frozen_content_hash,
                    size_bytes=source.size_bytes,
                    fingerprint=fingerprint,
                )
            )
            sources.append(source)
        outcome = PathResolutionOutcome.RESOLVED_MULTIPLE if len(records) > 1 else PathResolutionOutcome.RESOLVED
        return FrozenPathResolution(
            PathResolution(
                reference=updated,
                normalized_value=normalized,
                outcome=outcome,
                matches=tuple(records),
            ),
            tuple(sources),
        )

    def normalize(self, value: str, kind: PathReferenceKind) -> tuple[str | None, str | None]:
        if not value or not value.strip():
            return None, "Path reference cannot be empty."
        if any(ord(character) == 0 or ord(character) < 32 for character in value):
            return None, "Path reference contains a null or control character."
        raw = value.strip()
        if raw.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
            return None, "Windows device paths are not allowed."
        if raw.startswith(("\\\\", "//")):
            return None, "UNC and network paths are not allowed."
        windows = PureWindowsPath(raw)
        normalized_slashes = raw.replace("\\", "/")
        if windows.drive and not windows.is_absolute():
            return None, "Drive-relative paths are not allowed."
        if windows.is_absolute() or Path(raw).is_absolute():
            try:
                candidate = Path(raw).resolve(strict=False)
                relative = candidate.relative_to(self.root)
            except (OSError, ValueError):
                return None, "Absolute path is outside the selected repository root."
            normalized_slashes = relative.as_posix()
        components = normalized_slashes.split("/")
        if any(component == ".." for component in components):
            return None, "Path traversal is not allowed."
        normalized = posixpath.normpath(normalized_slashes)
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized in {"", "."}:
            return None, "Repository-root references are not allowed here."
        if normalized.startswith("../") or re.match(r"^[A-Za-z]:", normalized):
            return None, "Path escapes or changes the repository drive."
        if kind not in {PathReferenceKind.GLOB, PathReferenceKind.LOGICAL_SCOPE} and any(
            character in normalized for character in _WILDCARDS
        ):
            return None, "Wildcards are only valid for glob or logical-scope references."
        return normalized, None

    def _matches(self, normalized: str, kind: PathReferenceKind) -> tuple[tuple[InventoryEntry, ...], bool]:
        if kind in {PathReferenceKind.GLOB, PathReferenceKind.LOGICAL_SCOPE}:
            pattern = normalized.casefold() if self.policy.path_case_policy == "insensitive" else normalized
            values = tuple(
                entry
                for entry in self.inventory.entries
                if _glob_match(
                    entry.path.casefold() if self.policy.path_case_policy == "insensitive" else entry.path,
                    pattern,
                )
            )
            return values, False
        if kind == PathReferenceKind.EXACT_DIRECTORY:
            prefix = normalized.rstrip("/") + "/"
            values = tuple(
                entry
                for entry in self.inventory.entries
                if (
                    entry.path.casefold().startswith(prefix.casefold())
                    if self.policy.path_case_policy == "insensitive"
                    else entry.path.startswith(prefix)
                )
            )
            return values, False
        if self.policy.path_case_policy == "insensitive":
            case_matches = tuple(self.by_case.get(normalized.casefold(), ()))
            if case_matches:
                return case_matches, len(case_matches) > 1
        direct = self.by_path.get(normalized)
        if direct is not None:
            return (direct,), False
        return (), False

    def _freeze(self, entry: InventoryEntry, reference: PathReference) -> FrozenSource:
        target = (self.root / Path(entry.path)).resolve(strict=True)
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Resolved source escapes the repository root.") from exc
        if target.is_symlink():
            raise ValueError("Resolved source is an unsafe symbolic link.")
        raw = target.read_bytes()
        if len(raw) > self.policy.maximum_source_file_bytes:
            raise ValueError("Resolved source exceeds the per-file byte limit.")
        full_hash = hashlib.sha256(raw).hexdigest()
        if entry.content_hash is not None and full_hash != entry.content_hash:
            raise _StaleSourceError("Source changed after the repository inventory was frozen.")
        text = normalize_text(raw.decode("utf-8"))
        start, end = reference.line_start, reference.line_end
        if start is not None and end is not None:
            lines = text.splitlines(keepends=True)
            if start > len(lines) or end > len(lines):
                raise ValueError(f"Requested line range {start}-{end} exceeds the {len(lines)}-line source.")
            text = "".join(lines[start - 1 : end])
        frozen_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return FrozenSource(entry.path, text, full_hash, frozen_hash, start, end, len(raw))

    @staticmethod
    def _blocked_outcome(entries: tuple[InventoryEntry, ...]) -> PathResolutionOutcome | None:
        mapping = {
            InventoryClassification.BINARY: PathResolutionOutcome.BINARY,
            InventoryClassification.TOO_LARGE: PathResolutionOutcome.TOO_LARGE,
            InventoryClassification.UNSAFE_SYMLINK: PathResolutionOutcome.UNSAFE_SYMLINK,
            InventoryClassification.INVALID_ENCODING: PathResolutionOutcome.UNSUPPORTED,
            InventoryClassification.MISSING: PathResolutionOutcome.MISSING,
        }
        outcomes = [mapping[entry.classification] for entry in entries if entry.classification in mapping]
        return sorted(outcomes, key=lambda item: item.value)[0] if outcomes else None

    def _is_excluded(self, normalized: str) -> bool:
        segments = normalized.strip("/").split("/")
        excluded_names = {prefix.strip("/") for prefix in self.policy.excluded_prefixes}
        return any(segment in excluded_names for segment in segments) or any(
            normalized.endswith(suffix) for suffix in self.policy.excluded_suffixes
        )

    def _ignored_matches(self, normalized: str, kind: PathReferenceKind) -> bool:
        if kind in {PathReferenceKind.GLOB, PathReferenceKind.LOGICAL_SCOPE}:
            return any(_glob_match(path, normalized) for path in self.inventory.ignored_paths)
        prefix = normalized.rstrip("/") + "/"
        return any(
            path == normalized or (kind == PathReferenceKind.EXACT_DIRECTORY and path.startswith(prefix))
            for path in self.inventory.ignored_paths
        )


def _glob_match(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[: -len("/**")].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    collapsed = pattern.replace("/**/", "/")
    return (
        fnmatch.fnmatchcase(path, pattern)
        or (collapsed != pattern and fnmatch.fnmatchcase(path, collapsed))
        or (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]))
    )


def repository_glob_match(path: str, pattern: str) -> bool:
    """Expose the repository resolver's deterministic glob semantics."""
    return _glob_match(path, pattern)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
