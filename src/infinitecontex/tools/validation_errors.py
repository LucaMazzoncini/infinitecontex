"""Stable typed G4 validation errors."""


class ValidationExecutionError(ValueError):
    code = "validation_error"


class CommandDefinitionError(ValidationExecutionError):
    code = "command_definition_invalid"


class ExecutableTrustError(ValidationExecutionError):
    code = "executable_untrusted"


class ValidationParameterError(ValidationExecutionError):
    code = "invalid_parameter"


class ValidationAdmissionError(ValidationExecutionError):
    code = "validation_policy_rejected"


class ValidationApprovalError(ValidationAdmissionError):
    code = "validation_approval_missing"


class ValidationProcessError(ValidationExecutionError):
    code = "process_launch_failed"


class ValidationTimeoutError(ValidationExecutionError):
    code = "validation_timed_out"


class ValidationOutputLimitError(ValidationExecutionError):
    code = "validation_output_limit"


class RepositoryMutationDetectedError(ValidationExecutionError):
    code = "unexpected_repository_mutation"


class ValidationPersistenceError(ValidationExecutionError):
    code = "validation_record_persistence_failed"
