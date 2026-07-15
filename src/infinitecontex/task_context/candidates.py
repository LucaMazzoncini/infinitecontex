"""Explicit task declarations to deterministic context candidates."""

from __future__ import annotations

import hashlib
import re
from typing import Any

import orjson

from infinitecontex.context_budget.models import RetentionPriority
from infinitecontex.context_packing.models import (
    CandidateCategory,
    ChangeState,
    ContextCandidate,
    RelevanceSignals,
)
from infinitecontex.planning.models import Task
from infinitecontex.task_context.models import (
    PathReference,
    PathReferenceKind,
    ReferenceRequirement,
    RepositoryInventory,
    SymbolKind,
    SymbolReference,
)
from infinitecontex.task_context.paths import FrozenPathResolution, FrozenSource
from infinitecontex.task_context.symbols import FrozenSymbolResolution, symbol_reference_from_string

_RANGE_SUFFIX = re.compile(r"^(?P<path>.+)#L(?P<start>[0-9]+)-L?(?P<end>[0-9]+)$")


def task_path_references(task: Task) -> tuple[PathReference, ...]:
    references: list[PathReference] = []
    groups = (
        (
            task.context_requirements.required_files,
            "context_requirements.required_files",
            PathReferenceKind.EXACT_FILE,
            ReferenceRequirement.REQUIRED,
        ),
        (
            task.context_requirements.required_tests,
            "context_requirements.required_tests",
            PathReferenceKind.TEST_FILE,
            ReferenceRequirement.REQUIRED,
        ),
        (
            task.context_requirements.required_documentation,
            "context_requirements.required_documentation",
            PathReferenceKind.DOCUMENTATION_FILE,
            ReferenceRequirement.REQUIRED,
        ),
        (
            task.context_requirements.optional_files,
            "context_requirements.optional_files",
            PathReferenceKind.EXACT_FILE,
            ReferenceRequirement.OPTIONAL,
        ),
        (
            task.context_requirements.optional_tests,
            "context_requirements.optional_tests",
            PathReferenceKind.TEST_FILE,
            ReferenceRequirement.OPTIONAL,
        ),
        (
            task.context_requirements.optional_documentation,
            "context_requirements.optional_documentation",
            PathReferenceKind.DOCUMENTATION_FILE,
            ReferenceRequirement.OPTIONAL,
        ),
    )
    for values, field, default_kind, requirement in groups:
        for value in values:
            references.append(path_reference_from_string(value, task.task_id, field, default_kind, requirement))
    for declaration in task.context_requirements.path_references:
        references.append(
            PathReference(
                original_value=declaration.value,
                kind=PathReferenceKind(declaration.kind),
                requirement=ReferenceRequirement(declaration.requirement),
                source_task_id=task.task_id,
                source_field="context_requirements.path_references",
                line_start=declaration.line_start,
                line_end=declaration.line_end,
                expected_content_hash=declaration.expected_content_hash,
                expected_snapshot_fingerprint=declaration.expected_repository_snapshot,
                language_hint=declaration.language_hint,
                logical_label=declaration.logical_label,
            )
        )
    return tuple(sorted(references, key=lambda item: (item.source_field, item.original_value)))


def task_symbol_references(task: Task) -> tuple[SymbolReference, ...]:
    references = [
        symbol_reference_from_string(value, task.task_id, "context_requirements.required_symbols")
        for value in task.context_requirements.required_symbols
    ]
    references.extend(
        symbol_reference_from_string(
            value,
            task.task_id,
            "context_requirements.optional_symbols",
            ReferenceRequirement.OPTIONAL,
        )
        for value in task.context_requirements.optional_symbols
    )
    references.extend(
        SymbolReference(
            original_reference=item.reference,
            language=item.language,
            symbol_name=item.symbol_name,
            qualified_name=item.qualified_name,
            file_hint=item.file_hint,
            module_or_namespace=item.module_or_namespace,
            symbol_kind=SymbolKind(item.symbol_kind),
            signature_hint=item.signature_hint,
            line_start=item.line_start,
            line_end=item.line_end,
            requirement=ReferenceRequirement(item.requirement),
            source_task_id=task.task_id,
            source_field="context_requirements.symbol_references",
            expected_symbol_fingerprint=item.expected_symbol_fingerprint,
        )
        for item in task.context_requirements.symbol_references
    )
    return tuple(
        sorted(
            references, key=lambda item: (item.language, item.file_hint or "", item.qualified_name or item.symbol_name)
        )
    )


def path_reference_from_string(
    value: str,
    task_id: str,
    source_field: str,
    default_kind: PathReferenceKind = PathReferenceKind.EXACT_FILE,
    requirement: ReferenceRequirement = ReferenceRequirement.REQUIRED,
) -> PathReference:
    match = _RANGE_SUFFIX.fullmatch(value.strip())
    raw = match.group("path") if match else value
    if match:
        kind = PathReferenceKind.SOURCE_RANGE
    elif any(character in raw for character in "*?["):
        kind = PathReferenceKind.GLOB
    elif raw.endswith(("/", "\\")):
        kind = PathReferenceKind.EXACT_DIRECTORY
    else:
        kind = default_kind
    return PathReference(
        original_value=raw,
        kind=kind,
        requirement=requirement,
        source_task_id=task_id,
        source_field=source_field,
        line_start=int(match.group("start")) if match else None,
        line_end=int(match.group("end")) if match else None,
    )


class TaskContextCandidateBuilder:
    def build(
        self,
        task: Task,
        inventory: RepositoryInventory,
        paths: tuple[FrozenPathResolution, ...],
        symbols: tuple[FrozenSymbolResolution, ...],
    ) -> tuple[ContextCandidate, ...]:
        candidates: list[ContextCandidate] = [self._contract(task)]
        entry_by_path = {entry.path: entry for entry in inventory.entries}
        for resolved in paths:
            path_reference = resolved.resolution.reference
            if path_reference.requirement == ReferenceRequirement.FORBIDDEN:
                continue
            for source in resolved.sources:
                category = _path_category(path_reference.source_field, path_reference.kind)
                candidates.append(
                    self._source_candidate(
                        task,
                        source,
                        path_reference.requirement,
                        category,
                        path_reference.logical_label or source.path,
                        entry_by_path.get(source.path),
                        symbol_id=None,
                    )
                )
        for symbol_resolution in symbols:
            symbol_reference = symbol_resolution.resolution.reference
            if symbol_reference.requirement == ReferenceRequirement.FORBIDDEN:
                continue
            for match, source in zip(
                symbol_resolution.resolution.matches,
                symbol_resolution.sources,
                strict=True,
            ):
                candidates.append(
                    self._source_candidate(
                        task,
                        source,
                        symbol_reference.requirement,
                        CandidateCategory.SYMBOL_DEFINITION,
                        match.qualified_name,
                        entry_by_path.get(source.path),
                        symbol_id=match.symbol_fingerprint,
                    )
                )
        for index, diagnostic in enumerate(task.context_requirements.required_diagnostics):
            candidates.append(
                ContextCandidate(
                    candidate_id=_candidate_id(task.task_id, "diagnostic", str(index), diagnostic),
                    category=CandidateCategory.BUILD_OR_COMPILER_ERROR,
                    label=f"Declared diagnostic {index + 1}",
                    content=diagnostic,
                    logical_source=f"task:{task.task_id}:required-diagnostic:{index}",
                    mandatory=True,
                    retention_priority=RetentionPriority.REQUIRED,
                    direct_request_match=True,
                    relevance=RelevanceSignals(task_relevance=1000, source_confidence=1000),
                    deduplication_identity=f"diagnostic:{hashlib.sha256(diagnostic.encode()).hexdigest()}",
                    tie_break_key=f"diagnostic:{index:04d}",
                )
            )
        return tuple(sorted(candidates, key=lambda item: item.candidate_id))

    @staticmethod
    def _contract(task: Task) -> ContextCandidate:
        payload: dict[str, Any] = {
            "task_id": task.task_id,
            "task_key": task.task_key,
            "title": task.title,
            "objective": task.objective,
            "description": task.description,
            "task_type": task.task_type,
            "status": task.status,
            "priority": task.priority,
            "dependencies": task.dependency_ids,
            "soft_dependencies": task.soft_dependency_ids,
            "parent_task_id": task.parent_task_id,
            "acceptance_criteria": [item.model_dump(mode="json") for item in task.acceptance_criteria],
            "required_evidence": [item.model_dump(mode="json") for item in task.required_evidence],
            "declared_inputs": [item.model_dump(mode="json") for item in task.declared_inputs],
            "expected_outputs": [item.model_dump(mode="json") for item in task.expected_outputs],
            "affected_scopes": task.affected_scopes,
            "forbidden_scopes": task.forbidden_scopes,
            "context_requirements": task.context_requirements.model_dump(mode="json"),
            "context_class": task.context_class,
            "maximum_context_tokens": task.context_requirements.maximum_context_tokens,
            "requested_capabilities": task.requested_capabilities,
            "granted_capabilities": (),
            "risk_flags": task.risk_flags,
        }
        content = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2).decode("utf-8")
        return ContextCandidate(
            candidate_id=_candidate_id(task.task_id, "contract", task.task_fingerprint),
            category=CandidateCategory.CURRENT_TASK,
            label=f"Task contract: {task.title}",
            content=content,
            logical_source=f"plan-task:{task.task_id}",
            mandatory=True,
            retention_priority=RetentionPriority.REQUIRED,
            direct_request_match=True,
            relevance=RelevanceSignals(task_relevance=1000, source_confidence=1000),
            deduplication_identity=f"task-contract:{task.task_fingerprint}",
            tie_break_key="0000-task-contract",
        )

    @staticmethod
    def _source_candidate(
        task: Task,
        source: FrozenSource,
        requirement: ReferenceRequirement,
        category: CandidateCategory,
        label: str,
        entry: object | None,
        *,
        symbol_id: str | None,
    ) -> ContextCandidate:
        mandatory = requirement == ReferenceRequirement.REQUIRED
        modified = bool(getattr(entry, "modified", False))
        untracked = bool(getattr(entry, "untracked", False))
        staged = bool(getattr(entry, "staged", False))
        change_state = (
            ChangeState.MODIFIED if modified or staged else ChangeState.ADDED if untracked else ChangeState.UNCHANGED
        )
        range_value = f"{source.line_start}:{source.line_end}" if source.line_start else "whole"
        identity = f"source:{source.path}:{range_value}:{symbol_id or ''}:{source.frozen_content_hash}"
        return ContextCandidate(
            candidate_id=_candidate_id(task.task_id, identity),
            category=category,
            label=label,
            content=source.content,
            logical_source=identity,
            source_path=source.path,
            line_start=source.line_start,
            line_end=source.line_end,
            symbol_id=symbol_id,
            mandatory=mandatory,
            retention_priority=RetentionPriority.REQUIRED if mandatory else RetentionPriority.NORMAL,
            relevance=RelevanceSignals(task_relevance=1000, source_confidence=1000),
            direct_request_match=True,
            change_state=change_state,
            deduplication_identity=identity,
            tie_break_key=f"{source.path}:{range_value}:{symbol_id or ''}",
            content_hash=source.frozen_content_hash,
        )


def _path_category(source_field: str, kind: PathReferenceKind) -> CandidateCategory:
    if "tests" in source_field or kind == PathReferenceKind.TEST_FILE:
        return CandidateCategory.TESTS
    if "documentation" in source_field or kind in {
        PathReferenceKind.DOCUMENTATION_FILE,
        PathReferenceKind.CONFIGURATION_FILE,
    }:
        return CandidateCategory.REPOSITORY_DOCUMENTATION
    return CandidateCategory.SOURCE_CODE_EXCERPT


def _candidate_id(*values: str) -> str:
    digest = hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()
    return f"task-context-candidate-{digest[:24]}"
