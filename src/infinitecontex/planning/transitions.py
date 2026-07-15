"""Explicit deterministic task-status transition policy."""

from infinitecontex.planning.errors import PlanTransitionError
from infinitecontex.planning.models import TaskStatus

ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.DRAFT: frozenset({TaskStatus.READY, TaskStatus.CANCELLED}),
    TaskStatus.READY: frozenset({TaskStatus.BLOCKED, TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}),
    TaskStatus.BLOCKED: frozenset({TaskStatus.READY, TaskStatus.CANCELLED}),
    TaskStatus.IN_PROGRESS: frozenset({TaskStatus.AWAITING_REVIEW, TaskStatus.FAILED, TaskStatus.BLOCKED}),
    TaskStatus.AWAITING_REVIEW: frozenset({TaskStatus.COMPLETED, TaskStatus.IN_PROGRESS, TaskStatus.FAILED}),
    TaskStatus.FAILED: frozenset({TaskStatus.READY, TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset({TaskStatus.SUPERSEDED}),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.SUPERSEDED: frozenset(),
}


def validate_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise PlanTransitionError(
            f"Transition {current.value} -> {target.value} is not allowed; choose an explicit documented transition"
        )
