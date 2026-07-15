"""Task-to-tool scope eligibility using task-context path security."""

from __future__ import annotations

from infinitecontex.planning.models import Task
from infinitecontex.task_context.models import TaskContextAnalysis
from infinitecontex.task_context.policy import RepositoryResolutionPolicy
from infinitecontex.task_context.scopes import normalize_repository_scope, repository_scope_contains
from infinitecontex.tools.models import ScopeCheck, ScopeKind, ToolDefinition


def evaluate_tool_scopes(
    task: Task,
    definition: ToolDefinition,
    analysis: TaskContextAnalysis | None,
    policy: RepositoryResolutionPolicy | None = None,
) -> tuple[ScopeCheck, ...]:
    active = policy or RepositoryResolutionPolicy()
    affected, affected_errors = _normalize_many(task.affected_scopes)
    forbidden, forbidden_errors = _normalize_many(task.forbidden_scopes)
    checks: list[ScopeCheck] = []
    for scope in definition.scopes:
        if affected_errors or forbidden_errors:
            checks.append(
                ScopeCheck(
                    kind=scope.kind,
                    passed=False,
                    requested_values=tuple((*task.affected_scopes, *task.forbidden_scopes)),
                    normalized_values=tuple((*affected, *forbidden)),
                    reason_code="scope_escape",
                    detail="; ".join((*affected_errors, *forbidden_errors)),
                )
            )
            continue
        if scope.requires_resolved_task_context and analysis is None:
            checks.append(
                ScopeCheck(
                    kind=scope.kind,
                    passed=False,
                    reason_code="unresolved_scope",
                    detail="This scope requires a current task-context analysis.",
                )
            )
            continue
        if scope.kind in {
            ScopeKind.NONE,
            ScopeKind.GIT_METADATA,
            ScopeKind.INFCTX_ONLY_WRITE,
            ScopeKind.NETWORK_DESTINATION,
            ScopeKind.EXTERNAL_FILESYSTEM,
        }:
            checks.append(
                ScopeCheck(
                    kind=scope.kind,
                    passed=True,
                    requested_values=scope.declared_values,
                    normalized_values=scope.declared_values,
                    reason_code="scope_declared",
                    detail="The tool definition declares this non-repository scope explicitly.",
                )
            )
            continue
        if scope.kind in {
            ScopeKind.REPOSITORY_WIDE_READ,
            ScopeKind.EXPLICIT_PATH_READ,
            ScopeKind.EXPLICIT_SOURCE_RANGE_READ,
        }:
            blocked = bool(analysis and any(item.blocking for item in analysis.scope_conflicts))
            checks.append(
                ScopeCheck(
                    kind=scope.kind,
                    passed=not blocked,
                    requested_values=affected,
                    normalized_values=affected,
                    reason_code="forbidden_scope_overlap" if blocked else "read_scope_declared",
                    detail=(
                        "Current context analysis contains a blocking forbidden-scope conflict."
                        if blocked
                        else "Read scope is bounded by the current repository inventory and task context."
                    ),
                )
            )
            continue
        checks.append(_write_check(scope.kind, affected, forbidden, active))
    return tuple(checks)


def _write_check(
    kind: ScopeKind,
    affected: tuple[str, ...],
    forbidden: tuple[str, ...],
    policy: RepositoryResolutionPolicy,
) -> ScopeCheck:
    if not affected:
        return ScopeCheck(
            kind=kind,
            passed=False,
            reason_code="missing_affected_scope",
            detail="The task declares no affected scope for a future write.",
        )
    if kind == ScopeKind.TEST_ONLY_WRITE:
        eligible = tuple(value for value in affected if _test_scope(value))
    elif kind == ScopeKind.DOCUMENTATION_ONLY_WRITE:
        eligible = tuple(value for value in affected if _documentation_scope(value))
    else:
        eligible = affected
    if not eligible:
        return ScopeCheck(
            kind=kind,
            passed=False,
            requested_values=affected,
            normalized_values=affected,
            reason_code="scope_type_mismatch",
            detail=f"Affected scopes do not satisfy {kind.value} semantics.",
        )
    overlap = tuple(
        sorted(
            left
            for left in eligible
            for right in forbidden
            if repository_scope_contains(right, left, policy) or repository_scope_contains(left, right, policy)
        )
    )
    return ScopeCheck(
        kind=kind,
        passed=not overlap,
        requested_values=affected,
        normalized_values=eligible,
        reason_code="forbidden_scope_overlap" if overlap else "write_scope_bounded",
        detail=(
            f"Affected scope overlaps forbidden scope at {overlap[0]}."
            if overlap
            else "Future writes are bounded by explicit affected scopes."
        ),
    )


def _normalize_many(values: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    normalized: list[str] = []
    errors: list[str] = []
    for value in values:
        result, error = normalize_repository_scope(value)
        if error:
            errors.append(f"{value!r}: {error}")
        elif result is not None:
            normalized.append(result)
    return tuple(sorted(set(normalized))), tuple(sorted(errors))


def _test_scope(value: str) -> bool:
    folded = value.casefold()
    return folded.startswith(("tests/", "test/")) or "/tests/" in folded or "test_" in folded


def _documentation_scope(value: str) -> bool:
    folded = value.casefold()
    return folded.startswith(("docs/", "doc/")) or folded.endswith((".md", ".rst", ".txt"))
