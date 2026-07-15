"""Deterministic task scope-conflict analysis."""

from __future__ import annotations

import fnmatch

from infinitecontex.planning.models import Task
from infinitecontex.task_context.models import (
    PathResolution,
    PathResolutionOutcome,
    ReferenceRequirement,
    RepositoryInventory,
    ScopeConflict,
    ScopeConflictKind,
)
from infinitecontex.task_context.policy import RepositoryResolutionPolicy

_ROOT_WIDE = {"*", "**", "**/*", ".", "./"}


def validate_task_scopes(
    task: Task,
    inventory: RepositoryInventory,
    required_paths: tuple[PathResolution, ...],
    policy: RepositoryResolutionPolicy,
) -> tuple[ScopeConflict, ...]:
    affected = tuple(_normalize_scope(value) for value in task.affected_scopes)
    forbidden = tuple(_normalize_scope(value) for value in task.forbidden_scopes) + tuple(
        item.normalized_value
        for item in required_paths
        if item.reference.requirement == ReferenceRequirement.FORBIDDEN and item.normalized_value is not None
    )
    conflicts: list[ScopeConflict] = []
    conflicts.extend(_duplicates(affected, "affected"))
    conflicts.extend(_duplicates(forbidden, "forbidden"))
    conflicts.extend(_redundant(affected, "affected"))
    conflicts.extend(_redundant(forbidden, "forbidden"))
    for value in affected:
        if value in _ROOT_WIDE:
            conflicts.append(
                ScopeConflict(
                    kind=ScopeConflictKind.ROOT_WIDE_AFFECTED,
                    left=value,
                    blocking=True,
                    message="A repository-wide affected scope is unsafe and grants no permission.",
                )
            )
    entries = tuple(entry.path for entry in inventory.entries)
    for left in affected:
        for right in forbidden:
            left_matches = _scope_matches(left, entries, policy)
            right_matches = _scope_matches(right, entries, policy)
            intersection = sorted(left_matches & right_matches)
            if _same(left, right, policy):
                conflicts.append(
                    ScopeConflict(
                        kind=ScopeConflictKind.EXACT_OVERLAP,
                        left=left,
                        right=right,
                        blocking=True,
                        message="Affected and forbidden scopes are identical.",
                    )
                )
            elif intersection or _is_child_scope(left, right, policy):
                conflicts.append(
                    ScopeConflict(
                        kind=ScopeConflictKind.AFFECTED_UNDER_FORBIDDEN,
                        left=left,
                        right=right,
                        blocking=True,
                        message=(
                            "Affected scope intersects forbidden scope"
                            + (f" at {intersection[0]}." if intersection else ".")
                        ),
                    )
                )
            elif left.casefold() == right.casefold() and left != right:
                conflicts.append(
                    ScopeConflict(
                        kind=ScopeConflictKind.CASE_CONFLICT,
                        left=left,
                        right=right,
                        blocking=policy.path_case_policy == "insensitive",
                        message="Affected and forbidden scopes differ only by case.",
                    )
                )
    forbidden_files: set[str] = set()
    for scope in forbidden:
        forbidden_files.update(_scope_matches(scope, entries, policy))
    for resolution in required_paths:
        if resolution.outcome not in {PathResolutionOutcome.RESOLVED, PathResolutionOutcome.RESOLVED_MULTIPLE}:
            continue
        for match in resolution.matches:
            if match.path in forbidden_files:
                conflicts.append(
                    ScopeConflict(
                        kind=ScopeConflictKind.REQUIRED_FILE_FORBIDDEN,
                        left=match.path,
                        right=resolution.reference.original_value,
                        blocking=True,
                        message="A required context file is excluded by a forbidden scope.",
                    )
                )
    for output in task.expected_outputs:
        if not output.reference:
            continue
        normalized = _normalize_scope(output.reference)
        unsafe = (
            normalized.startswith(("/", "//"))
            or ":/" in normalized
            or any(part == ".." for part in normalized.split("/"))
        )
        if unsafe or (affected and not any(_path_within_scope(normalized, scope, policy) for scope in affected)):
            conflicts.append(
                ScopeConflict(
                    kind=ScopeConflictKind.OUTPUT_ESCAPE,
                    left=normalized,
                    blocking=True,
                    message="Declared output is unsafe or outside every affected scope.",
                )
            )
    return tuple(sorted(set(conflicts), key=lambda item: (item.kind, item.left, item.right or "", item.message)))


def _duplicates(values: tuple[str, ...], label: str) -> list[ScopeConflict]:
    seen: set[str] = set()
    results: list[ScopeConflict] = []
    for value in sorted(values):
        if value in seen:
            results.append(
                ScopeConflict(
                    kind=ScopeConflictKind.DUPLICATE_SCOPE,
                    left=value,
                    blocking=False,
                    message=f"Duplicate {label} scope is redundant.",
                )
            )
        seen.add(value)
    return results


def _redundant(values: tuple[str, ...], label: str) -> list[ScopeConflict]:
    results: list[ScopeConflict] = []
    unique = sorted(set(values))
    for index, value in enumerate(unique):
        for other in unique[:index] + unique[index + 1 :]:
            if value != other and value.startswith(other.rstrip("/") + "/"):
                results.append(
                    ScopeConflict(
                        kind=ScopeConflictKind.REDUNDANT_SCOPE,
                        left=value,
                        right=other,
                        blocking=False,
                        message=f"{label.title()} scope is contained by another declared scope.",
                    )
                )
                break
    return results


def _scope_matches(scope: str, entries: tuple[str, ...], policy: RepositoryResolutionPolicy) -> set[str]:
    if any(character in scope for character in "*?["):
        return {path for path in entries if _glob(path, scope, policy)}
    prefix = scope.rstrip("/") + "/"
    return {path for path in entries if _same(path, scope, policy) or _starts(path, prefix, policy)}


def _path_within_scope(path: str, scope: str, policy: RepositoryResolutionPolicy) -> bool:
    return bool(_scope_matches(scope, (path,), policy))


def _is_child_scope(child: str, parent: str, policy: RepositoryResolutionPolicy) -> bool:
    if any(character in parent for character in "*?["):
        return _glob(child, parent, policy)
    return _starts(child, parent.rstrip("/") + "/", policy)


def _glob(path: str, pattern: str, policy: RepositoryResolutionPolicy) -> bool:
    left, right = (path.casefold(), pattern.casefold()) if policy.path_case_policy == "insensitive" else (path, pattern)
    if right.endswith("/**"):
        prefix = right[: -len("/**")].rstrip("/")
        return left == prefix or left.startswith(prefix + "/")
    return fnmatch.fnmatchcase(left, right) or (right.startswith("**/") and fnmatch.fnmatchcase(left, right[3:]))


def _same(left: str, right: str, policy: RepositoryResolutionPolicy) -> bool:
    return left.casefold() == right.casefold() if policy.path_case_policy == "insensitive" else left == right


def _starts(value: str, prefix: str, policy: RepositoryResolutionPolicy) -> bool:
    if policy.path_case_policy == "insensitive":
        return value.casefold().startswith(prefix.casefold())
    return value.startswith(prefix)


def _normalize_scope(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized
