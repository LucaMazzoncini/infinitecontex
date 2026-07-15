"""Iterative deterministic task-DAG validation and readiness analysis."""

from __future__ import annotations

import heapq
from collections import deque
from typing import Literal

from infinitecontex.planning.models import (
    CycleDetail,
    ReadinessAnalysis,
    Task,
    TaskStatus,
    TaskType,
    ValidationIssue,
)

_TYPE_ORDER = {value: index for index, value in enumerate(TaskType)}
_SATISFIED = {TaskStatus.COMPLETED, TaskStatus.SUPERSEDED}


class DagAnalysis:
    def __init__(self, tasks: tuple[Task, ...]) -> None:
        self.tasks = tasks
        self.by_id = {task.task_id: task for task in tasks}
        self.predecessors = {task.task_id: set(task.dependency_ids) for task in tasks}
        self.successors: dict[str, set[str]] = {task.task_id: set() for task in tasks}
        for task in tasks:
            for dependency in task.dependency_ids:
                if dependency in self.successors:
                    self.successors[dependency].add(task.task_id)

    def validate(self) -> tuple[tuple[ValidationIssue, ...], tuple[CycleDetail, ...]]:
        issues: list[ValidationIssue] = []
        for task in sorted(self.tasks, key=lambda item: item.task_id):
            for dependency in task.dependency_ids:
                if dependency not in self.by_id:
                    issues.append(
                        _issue("missing_dependency", task.task_id, f"Hard dependency {dependency} does not exist")
                    )
                elif dependency == task.task_id:
                    issues.append(_issue("self_dependency", task.task_id, "A task cannot depend on itself"))
            for dependency in task.soft_dependency_ids:
                if dependency not in self.by_id:
                    issues.append(
                        _issue("missing_soft_dependency", task.task_id, f"Soft dependency {dependency} does not exist")
                    )
                elif dependency == task.task_id:
                    issues.append(_issue("self_soft_dependency", task.task_id, "A task cannot softly depend on itself"))
            if task.status == TaskStatus.COMPLETED:
                unfinished = sorted(
                    dependency
                    for dependency in task.dependency_ids
                    if dependency in self.by_id and self.by_id[dependency].status not in _SATISFIED
                )
                if unfinished:
                    issues.append(
                        _issue(
                            "completed_with_unfinished_prerequisite",
                            task.task_id,
                            f"Completed task has unfinished prerequisites: {', '.join(unfinished)}",
                        )
                    )
            if task.status == TaskStatus.READY:
                unfinished = sorted(
                    dependency
                    for dependency in task.dependency_ids
                    if dependency in self.by_id and self.by_id[dependency].status not in _SATISFIED
                )
                if unfinished:
                    issues.append(
                        _issue(
                            "ready_with_unfinished_prerequisite",
                            task.task_id,
                            f"Ready task has unfinished prerequisites: {', '.join(unfinished)}",
                        )
                    )
        cycles = list(self._cycles(self.predecessors, "dependency_cycle"))
        soft = {task.task_id: set(task.soft_dependency_ids) for task in self.tasks}
        cycles.extend(self._cycles(soft, "soft_dependency_cycle"))
        parent = {task.task_id: ({task.parent_task_id} if task.parent_task_id else set()) for task in self.tasks}
        for task in self.tasks:
            if task.parent_task_id and task.parent_task_id not in self.by_id:
                issues.append(_issue("missing_parent", task.task_id, f"Parent {task.parent_task_id} does not exist"))
        cycles.extend(self._cycles(parent, "parent_cycle"))
        return tuple(sorted(issues, key=lambda item: (item.code, item.task_id or ""))), tuple(
            sorted(cycles, key=lambda item: (item.code, item.canonical_path))
        )

    def topological_order(self) -> tuple[str, ...]:
        indegree = {
            task_id: len({item for item in dependencies if item in self.by_id})
            for task_id, dependencies in self.predecessors.items()
        }
        heap: list[tuple[int, int, int, str, str, str]] = []
        depths = {task_id: 0 for task_id in self.by_id}
        for task_id, degree in indegree.items():
            if degree == 0:
                heapq.heappush(heap, (0, *self._sort_key(self.by_id[task_id])))
        ordered: list[str] = []
        while heap:
            key = heapq.heappop(heap)
            task_id = key[4]
            ordered.append(task_id)
            for successor in sorted(self.successors[task_id]):
                indegree[successor] -= 1
                depths[successor] = max(depths[successor], depths[task_id] + 1)
                if indegree[successor] == 0:
                    heapq.heappush(heap, (depths[successor], *self._sort_key(self.by_id[successor])))
        return tuple(ordered)

    def readiness(self, topological: tuple[str, ...]) -> ReadinessAnalysis:
        depths: dict[str, int] = {}
        for task_id in topological:
            predecessors = self.predecessors[task_id]
            depths[task_id] = max((depths.get(item, 0) + 1 for item in predecessors), default=0)
        roots = tuple(sorted(task_id for task_id, values in self.predecessors.items() if not values))
        leaves = tuple(sorted(task_id for task_id, values in self.successors.items() if not values))
        isolated = tuple(sorted(set(roots) & set(leaves)))
        ready = tuple(
            task.task_id
            for task in sorted(self.tasks, key=self._sort_key)
            if task.status == TaskStatus.READY
            and all(self.by_id[item].status in _SATISFIED for item in task.dependency_ids if item in self.by_id)
        )
        dependency_blocked = tuple(
            task.task_id
            for task in sorted(self.tasks, key=self._sort_key)
            if task.status not in _SATISFIED
            and task.status != TaskStatus.BLOCKED
            and any(self.by_id[item].status not in _SATISFIED for item in task.dependency_ids if item in self.by_id)
        )
        explicitly_blocked = tuple(sorted(task.task_id for task in self.tasks if task.status == TaskStatus.BLOCKED))
        completed_closure = tuple(sorted(task.task_id for task in self.tasks if task.status in _SATISFIED))
        downstream = {task_id: tuple(sorted(self.successors[task_id])) for task_id in sorted(self.by_id)}
        return ReadinessAnalysis(
            ready_task_ids=ready,
            dependency_blocked_task_ids=dependency_blocked,
            explicitly_blocked_task_ids=explicitly_blocked,
            completed_prerequisite_closure=completed_closure,
            roots=roots,
            leaves=leaves,
            isolated=isolated,
            downstream_dependents=downstream,
            dependency_depths=depths,
            maximum_dependency_depth=max(depths.values(), default=0),
        )

    def _cycles(
        self,
        dependencies: dict[str, set[str]],
        code: Literal["dependency_cycle", "soft_dependency_cycle", "parent_cycle"],
    ) -> tuple[CycleDetail, ...]:
        valid = {key: {item for item in value if item in dependencies} for key, value in dependencies.items()}
        indegree = {key: len(value) for key, value in valid.items()}
        reverse: dict[str, set[str]] = {key: set() for key in valid}
        for task_id, values in valid.items():
            for dependency in values:
                reverse[dependency].add(task_id)
        queue = deque(sorted(key for key, value in indegree.items() if value == 0))
        while queue:
            current = queue.popleft()
            for successor in sorted(reverse[current]):
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    queue.append(successor)
        remaining = {key for key, value in indegree.items() if value > 0}
        cycles: list[CycleDetail] = []
        while remaining:
            start = min(remaining)
            positions: dict[str, int] = {}
            path: list[str] = []
            current = start
            while current not in positions:
                positions[current] = len(path)
                path.append(current)
                choices = sorted(item for item in valid[current] if item in remaining)
                if not choices:
                    break
                current = choices[0]
            if current in positions:
                raw = path[positions[current] :]
                canonical = _canonical_cycle(raw)
                cycles.append(
                    CycleDetail(
                        code=code,
                        involved_task_ids=tuple(sorted(raw)),
                        canonical_path=canonical,
                        remediation="Remove or redirect one reported dependency edge and validate again.",
                    )
                )
                remaining.difference_update(raw)
            else:
                remaining.discard(start)
        return tuple(cycles)

    @staticmethod
    def _sort_key(task: Task) -> tuple[int, int, str, str, str]:
        return (
            -int(task.priority),
            _TYPE_ORDER[task.task_type],
            task.task_key.casefold(),
            task.task_id,
            task.task_fingerprint,
        )


def _canonical_cycle(values: list[str]) -> tuple[str, ...]:
    rotations = [tuple(values[index:] + values[:index]) for index in range(len(values))]
    best = min(rotations)
    return (*best, best[0])


def _issue(code: str, task_id: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        message=message,
        task_id=task_id,
        remediation="Correct the task declaration and validate the complete plan again.",
    )
