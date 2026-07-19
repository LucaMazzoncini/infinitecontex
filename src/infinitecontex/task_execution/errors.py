"""Stable fail-closed G7 errors."""


class TaskExecutionError(RuntimeError):
    code = "task_execution_error"


class AuthorizationDenied(TaskExecutionError):
    code = "authorization_denied"


class GrantRejected(TaskExecutionError):
    code = "grant_rejected"


class ActionRejected(TaskExecutionError):
    code = "action_rejected"


class AllowanceExceeded(ActionRejected):
    code = "allowance_exceeded"


class PersistenceError(TaskExecutionError):
    code = "persistence_failure"
