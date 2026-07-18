"""Typed fail-closed G2 execution errors."""


class ToolExecutionError(RuntimeError):
    code = "tool_execution_error"


class ExecutionAdmissionError(ToolExecutionError):
    code = "execution_admission_denied"


class MissingHandlerError(ExecutionAdmissionError):
    code = "missing_handler"


class HandlerFingerprintMismatchError(ExecutionAdmissionError):
    code = "handler_fingerprint_mismatch"


class InvalidGrantError(ExecutionAdmissionError):
    code = "invalid_read_grant"


class StaleRepositorySnapshotError(ExecutionAdmissionError):
    code = "stale_repository_snapshot"


class ForbiddenRepositoryPathError(ExecutionAdmissionError):
    code = "forbidden_repository_path"


class SensitiveRepositoryPathError(ExecutionAdmissionError):
    code = "sensitive_repository_path"


class TaskBoundAdmissionError(ExecutionAdmissionError):
    code = "task_bound_admission_denied"


class MalformedExecutionRecordError(ToolExecutionError):
    code = "malformed_execution_record"


class ExecutionPersistenceError(ToolExecutionError):
    code = "execution_persistence_failure"
