"""Strict plan parsing, normalization, validation, import, and revision orchestration."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from infinitecontex.planning.dag import DagAnalysis
from infinitecontex.planning.errors import PlanFormatError, PlanNotFoundError, PlanValidationError
from infinitecontex.planning.models import (
    MAX_PLAN_FILE_BYTES,
    PlanInput,
    PlannerProvenance,
    PlanRevision,
    PlanValidationReport,
    Task,
    TaskInput,
    TaskStatus,
    ValidationIssue,
)
from infinitecontex.planning.normalization import (
    generate_plan_id,
    generate_task_id,
    graph_fingerprint,
    objective_hash,
    revision_fingerprint,
    semantic_plan_fingerprint,
    sha256_payload,
    task_fingerprint_payload,
)
from infinitecontex.planning.store import PlanStore
from infinitecontex.planning.transitions import validate_transition


class PlanningService:
    def __init__(self, store: PlanStore, clock: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.clock = clock or (lambda: datetime.now(UTC))

    def parse_file(self, path: Path, *, allow_large_file: bool = False) -> PlanInput:
        data = path.read_bytes()
        if len(data) > MAX_PLAN_FILE_BYTES and not allow_large_file:
            raise PlanFormatError(
                f"Plan file is {len(data)} bytes; the safe limit is {MAX_PLAN_FILE_BYTES} bytes "
                "(pass --allow-large-file only for a reviewed plan)"
            )
        return self.parse_json(data)

    @staticmethod
    def parse_json(data: bytes) -> PlanInput:
        try:
            text = data.decode("utf-8")
            _ensure_safe_json_nesting(text)
            raw = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
            if not isinstance(raw, dict):
                raise PlanFormatError("Plan JSON must contain exactly one object")
            return PlanInput.model_validate(raw)
        except PlanFormatError:
            raise
        except (UnicodeError, json.JSONDecodeError, RecursionError, ValidationError) as exc:
            raise PlanFormatError(f"Plan JSON is invalid and was not repaired: {exc}") from exc

    def validate(self, plan: PlanInput) -> tuple[PlanRevision, PlanValidationReport]:
        revision = self._build_revision(
            plan, previous=None, reason="Initial strict plan import", author=plan.planner_provenance
        )
        return revision, self._report(revision)

    def import_plan(
        self,
        plan: PlanInput,
        *,
        revision_reason: str = "Explicit plan import",
        revision_author: PlannerProvenance | None = None,
    ) -> tuple[PlanRevision, PlanValidationReport, bool]:
        plan_id = plan.plan_id or generate_plan_id(plan)
        try:
            previous = self.store.load_current(plan_id)
        except PlanNotFoundError:
            previous = None
        revision = self._build_revision(
            plan,
            previous=previous,
            reason=revision_reason,
            author=revision_author or plan.planner_provenance,
        )
        report = self._report(revision)
        if not report.valid:
            raise PlanValidationError(
                f"Plan is invalid ({report.errors[0].code if report.errors else 'cycle'}); no revision was persisted"
            )
        if previous is not None and semantic_plan_fingerprint(previous) == semantic_plan_fingerprint(revision):
            return previous, self._report(previous), False
        self.store.save_revision(revision)
        return revision, report, True

    def transition_task(
        self,
        plan_id: str,
        task_id: str,
        target: TaskStatus,
        *,
        reason: str,
        author: PlannerProvenance,
    ) -> PlanRevision:
        current = self.store.load_current(plan_id)
        existing = next((task for task in current.tasks if task.task_id == task_id), None)
        if existing is None:
            raise PlanValidationError(f"Task {task_id} does not belong to plan {plan_id}")
        validate_transition(existing.status, target)
        inputs = tuple(
            _task_to_input(task, status=target if task.task_id == task_id else task.status) for task in current.tasks
        )
        plan = _revision_to_input(current, inputs)
        revision, _, persisted = self.import_plan(plan, revision_reason=reason, revision_author=author)
        if not persisted:
            raise PlanValidationError("Status transition did not change the semantic plan")
        return revision

    def inspect(self, plan_id: str, revision: int | None = None) -> tuple[PlanRevision, PlanValidationReport]:
        plan = self.store.load_current(plan_id) if revision is None else self.store.load_revision(plan_id, revision)
        return plan, self._report(plan)

    def _build_revision(
        self,
        plan: PlanInput,
        *,
        previous: PlanRevision | None,
        reason: str,
        author: PlannerProvenance,
    ) -> PlanRevision:
        now = self.clock()
        if now.tzinfo is None:
            raise PlanValidationError("planning clock must return a timezone-aware timestamp")
        plan_id = previous.plan_id if previous else (plan.plan_id or generate_plan_id(plan))
        if plan.plan_id is not None and plan.plan_id != plan_id:
            raise PlanValidationError("A new revision must retain the stable plan ID")
        task_keys = [task.task_key.casefold() for task in plan.tasks]
        if len(set(task_keys)) != len(task_keys):
            raise PlanValidationError("Task keys must be unique within a plan")
        id_by_key = {
            task.task_key.casefold(): task.task_id or generate_task_id(plan_id, task.task_key) for task in plan.tasks
        }
        if len(set(id_by_key.values())) != len(id_by_key):
            raise PlanValidationError("Task IDs must be unique within a plan")
        previous_tasks = {task.task_id: task for task in previous.tasks} if previous else {}
        tasks = tuple(
            sorted(
                (self._normalize_task(task, plan_id, id_by_key, now, previous_tasks) for task in plan.tasks),
                key=lambda item: item.task_id,
            )
        )
        graph = graph_fingerprint(tasks)
        revision_number = previous.current_revision + 1 if previous else 1
        previous_by_id = {task.task_id: task for task in previous.tasks} if previous else {}
        current_by_id = {task.task_id: task for task in tasks}
        changed = tuple(
            sorted(
                task_id
                for task_id in set(previous_by_id) | set(current_by_id)
                if previous_by_id.get(task_id) != current_by_id.get(task_id)
                and (
                    previous_by_id.get(task_id) is None
                    or current_by_id.get(task_id) is None
                    or previous_by_id[task_id].task_fingerprint != current_by_id[task_id].task_fingerprint
                )
            )
        )
        structural = _structural_changes(previous, tasks)
        provisional = PlanRevision(
            plan_id=plan_id,
            stable_plan_key=plan.stable_plan_key,
            title=plan.title,
            objective=plan.objective,
            normalized_objective_hash=objective_hash(plan.objective),
            status=plan.status,
            planner_provenance=plan.planner_provenance,
            created_at=previous.created_at if previous else now,
            updated_at=now,
            current_revision=revision_number,
            previous_revision_fingerprint=previous.revision_fingerprint if previous else None,
            revision_fingerprint="0" * 64,
            revision_reason=reason,
            revision_author=author,
            changed_task_ids=changed,
            structural_changes=structural,
            superseded_revision=previous.current_revision if previous else None,
            parent_plan_id=plan.parent_plan_id,
            originating_request_ref=plan.originating_request_ref,
            repository_ref=plan.repository_ref,
            target_branch=plan.target_branch,
            model_profile_ref=plan.model_profile_ref,
            task_count=len(tasks),
            graph_fingerprint=graph,
            warnings=tuple(sorted(plan.warnings)),
            metadata=dict(sorted(plan.metadata.items())),
            tasks=tasks,
        )
        return provisional.model_copy(update={"revision_fingerprint": revision_fingerprint(provisional)})

    @staticmethod
    def _normalize_task(
        task: TaskInput,
        plan_id: str,
        id_by_key: dict[str, str],
        now: datetime,
        previous_tasks: dict[str, Task],
    ) -> Task:
        task_id = task.task_id or generate_task_id(plan_id, task.task_key)
        dependency_ids = tuple(
            sorted(id_by_key.get(key.casefold(), generate_task_id(plan_id, key)) for key in task.dependency_keys)
        )
        soft_ids = tuple(
            sorted(id_by_key.get(key.casefold(), generate_task_id(plan_id, key)) for key in task.soft_dependency_keys)
        )
        parent_id = (
            id_by_key.get(task.parent_task_key.casefold(), generate_task_id(plan_id, task.parent_task_key))
            if task.parent_task_key
            else None
        )
        fingerprint = sha256_payload(task_fingerprint_payload(task, dependency_ids, soft_ids, parent_id))
        old = previous_tasks.get(task_id)
        return Task(
            task_id=task_id,
            plan_id=plan_id,
            task_key=task.task_key,
            title=task.title,
            objective=task.objective,
            description=task.description,
            task_type=task.task_type,
            status=task.status,
            priority=task.priority,
            dependency_ids=dependency_ids,
            soft_dependency_ids=soft_ids,
            parent_task_id=parent_id,
            acceptance_criteria=tuple(sorted(task.acceptance_criteria, key=lambda item: item.criterion_id)),
            required_evidence=tuple(sorted(task.required_evidence, key=lambda item: item.evidence_id)),
            declared_inputs=tuple(sorted(task.declared_inputs, key=lambda item: item.name)),
            expected_outputs=tuple(sorted(task.expected_outputs, key=lambda item: item.name)),
            affected_scopes=tuple(sorted(task.affected_scopes)),
            forbidden_scopes=tuple(sorted(task.forbidden_scopes)),
            context_requirements=task.context_requirements,
            complexity=task.complexity,
            context_class=task.context_class,
            estimated_context_tokens=task.estimated_context_tokens,
            requested_capabilities=tuple(sorted(task.requested_capabilities)),
            granted_capabilities=(),
            risk_flags=tuple(sorted(task.risk_flags)),
            retry_policy=task.retry_policy,
            provenance=task.provenance,
            metadata=dict(sorted(task.metadata.items())),
            created_at=old.created_at if old else now,
            updated_at=old.updated_at if old and old.task_fingerprint == fingerprint else now,
            task_fingerprint=fingerprint,
        )

    @staticmethod
    def _report(plan: PlanRevision) -> PlanValidationReport:
        task_ids = [task.task_id for task in plan.tasks]
        issues: list[ValidationIssue] = []
        if len(set(task_ids)) != len(task_ids):
            issues.append(
                ValidationIssue(
                    code="duplicate_task_id",
                    message="Task IDs must be unique",
                    remediation="Assign unique stable task keys and IDs.",
                )
            )
        if any(task.plan_id != plan.plan_id for task in plan.tasks):
            issues.append(
                ValidationIssue(
                    code="task_plan_mismatch",
                    message="Every task must belong to exactly one plan",
                    remediation="Rebuild tasks under the selected plan ID.",
                )
            )
        if plan.task_count != len(plan.tasks):
            issues.append(
                ValidationIssue(
                    code="task_count_mismatch",
                    message="Persisted task count is inconsistent",
                    remediation="Re-import the complete plan.",
                )
            )
        dag = DagAnalysis(plan.tasks)
        dag_issues, cycles = dag.validate()
        issues.extend(dag_issues)
        topological = dag.topological_order() if not cycles else ()
        readiness = dag.readiness(topological) if len(topological) == len(plan.tasks) else None
        capabilities: dict[str, int] = {}
        for task in plan.tasks:
            for capability in task.requested_capabilities:
                capabilities[capability.value] = capabilities.get(capability.value, 0) + 1
        scope_summary = {
            "affected": sum(len(task.affected_scopes) for task in plan.tasks),
            "forbidden": sum(len(task.forbidden_scopes) for task in plan.tasks),
        }
        return PlanValidationReport(
            valid=not issues and not cycles,
            plan_id=plan.plan_id,
            graph_fingerprint=plan.graph_fingerprint,
            task_count=len(plan.tasks),
            edge_count=sum(len(task.dependency_ids) for task in plan.tasks),
            root_count=len(readiness.roots) if readiness else 0,
            leaf_count=len(readiness.leaves) if readiness else 0,
            maximum_dependency_depth=readiness.maximum_dependency_depth if readiness else 0,
            ready_task_count=len(readiness.ready_task_ids) if readiness else 0,
            blocked_task_count=(len(readiness.dependency_blocked_task_ids) + len(readiness.explicitly_blocked_task_ids))
            if readiness
            else 0,
            errors=tuple(sorted(issues, key=lambda item: (item.code, item.task_id or ""))),
            warnings=plan.warnings,
            normalized_changes=("sorted unordered task declarations and normalized generated identities",),
            detected_cycles=cycles,
            capability_summary=dict(sorted(capabilities.items())),
            scope_summary=scope_summary,
            topological_task_ids=topological,
            readiness=readiness,
        )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PlanFormatError(f"Duplicate JSON object key {key!r} is not allowed")
        result[key] = value
    return result


def _ensure_safe_json_nesting(text: str, maximum_depth: int = 128) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > maximum_depth:
                raise PlanFormatError(
                    f"Plan JSON nesting exceeds the safe limit of {maximum_depth}; simplify the document"
                )
        elif character in "]}":
            depth -= 1


def _task_to_input(task: Task, *, status: TaskStatus) -> TaskInput:
    # Dependency keys are reconstructed later through the plan-wide ID map.
    return TaskInput(
        task_id=task.task_id,
        task_key=task.task_key,
        title=task.title,
        objective=task.objective,
        description=task.description,
        task_type=task.task_type,
        status=status,
        priority=task.priority,
        acceptance_criteria=task.acceptance_criteria,
        required_evidence=task.required_evidence,
        declared_inputs=task.declared_inputs,
        expected_outputs=task.expected_outputs,
        affected_scopes=task.affected_scopes,
        forbidden_scopes=task.forbidden_scopes,
        context_requirements=task.context_requirements,
        complexity=task.complexity,
        context_class=task.context_class,
        estimated_context_tokens=task.estimated_context_tokens,
        requested_capabilities=task.requested_capabilities,
        risk_flags=task.risk_flags,
        retry_policy=task.retry_policy,
        provenance=task.provenance,
        metadata=task.metadata,
    )


def _revision_to_input(revision: PlanRevision, tasks: tuple[TaskInput, ...]) -> PlanInput:
    key_by_id = {task.task_id: task.task_key for task in revision.tasks}
    converted: list[TaskInput] = []
    by_id = {task.task_id: task for task in revision.tasks}
    for task in tasks:
        original = by_id[task.task_id or ""]
        converted.append(
            task.model_copy(
                update={
                    "dependency_keys": tuple(key_by_id[item] for item in original.dependency_ids),
                    "soft_dependency_keys": tuple(key_by_id[item] for item in original.soft_dependency_ids),
                    "parent_task_key": key_by_id.get(original.parent_task_id or ""),
                }
            )
        )
    return PlanInput(
        plan_id=revision.plan_id,
        stable_plan_key=revision.stable_plan_key,
        title=revision.title,
        objective=revision.objective,
        status=revision.status,
        planner_provenance=revision.planner_provenance,
        parent_plan_id=revision.parent_plan_id,
        originating_request_ref=revision.originating_request_ref,
        repository_ref=revision.repository_ref,
        target_branch=revision.target_branch,
        model_profile_ref=revision.model_profile_ref,
        warnings=revision.warnings,
        metadata=revision.metadata,
        tasks=tuple(converted),
    )


def _structural_changes(previous: PlanRevision | None, tasks: tuple[Task, ...]) -> tuple[str, ...]:
    if previous is None:
        return (f"created plan with {len(tasks)} tasks",)
    old_ids = {task.task_id for task in previous.tasks}
    new_ids = {task.task_id for task in tasks}
    changes = []
    if new_ids - old_ids:
        changes.append(f"added {len(new_ids - old_ids)} tasks")
    if old_ids - new_ids:
        changes.append(f"removed {len(old_ids - new_ids)} tasks")
    if previous.graph_fingerprint != graph_fingerprint(tasks):
        changes.append("changed task declarations or dependency graph")
    return tuple(changes or ["updated plan semantic metadata"])
