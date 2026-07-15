"""Typed failures for the data-only tool registry and policy inspection."""


class ToolPolicyError(RuntimeError):
    """Base G1 tool-inspection failure."""


class MalformedToolDefinitionError(ToolPolicyError):
    pass


class DuplicateToolIdentityError(ToolPolicyError):
    pass


class ConflictingToolFingerprintError(ToolPolicyError):
    pass


class UnsupportedRegistrySchemaError(ToolPolicyError):
    pass


class UnknownCapabilityError(ToolPolicyError):
    pass


class UnknownEffectError(ToolPolicyError):
    pass


class ContradictoryEffectsError(ToolPolicyError):
    pass


class InvalidScopeSemanticsError(ToolPolicyError):
    pass


class RegistryUnavailableError(ToolPolicyError):
    pass


class ToolMissingError(ToolPolicyError):
    pass


class ToolUnavailableError(ToolPolicyError):
    pass


class ToolTaskMissingError(ToolPolicyError):
    pass


class ToolTaskContextMissingError(ToolPolicyError):
    pass


class StaleToolTaskContextError(ToolPolicyError):
    pass


class ToolPlanRevisionMismatchError(ToolPolicyError):
    pass


class ForbiddenToolError(ToolPolicyError):
    pass


class ToolCapabilityMismatchError(ToolPolicyError):
    pass


class ToolScopeMismatchError(ToolPolicyError):
    pass


class UnsupportedToolPolicyVersionError(ToolPolicyError):
    pass


class MalformedToolDecisionError(ToolPolicyError):
    pass


class StaleToolDecisionError(ToolPolicyError):
    pass


class ToolDecisionPersistenceError(ToolPolicyError):
    pass
