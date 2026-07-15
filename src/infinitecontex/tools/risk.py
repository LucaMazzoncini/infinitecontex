"""Effect-derived minimum risk and future approval requirements."""

from infinitecontex.tools.models import ApprovalClass, AvailabilityState, RiskLevel, ToolDefinition, ToolEffects

_RISK_ORDER = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


def risk_rank(value: RiskLevel) -> int:
    return _RISK_ORDER[value]


def derive_minimum_risk(effects: ToolEffects) -> RiskLevel:
    if any(
        (
            effects.deletes_data,
            effects.rewrites_git_history,
            effects.irreversible,
            effects.accesses_secrets,
            effects.requires_elevated_privileges,
            effects.accesses_paths_outside_repository,
        )
    ):
        return RiskLevel.CRITICAL
    if any(
        (
            effects.runs_local_processes,
            effects.accesses_network,
            effects.modifies_git_index,
            effects.creates_commits,
            effects.pushes_remotely,
            effects.invokes_model,
        )
    ):
        return RiskLevel.HIGH
    if any(
        (
            effects.writes_repository_files,
            effects.writes_infctx,
            effects.mutates_plans,
            effects.mutates_task_status,
            effects.creates_artifacts,
        )
    ):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def derive_approval_class(definition: ToolDefinition) -> ApprovalClass:
    effects = definition.effects
    if definition.availability == AvailabilityState.PERMANENTLY_FORBIDDEN or any(
        (effects.deletes_data, effects.rewrites_git_history, effects.accesses_secrets)
    ):
        return ApprovalClass.PERMANENTLY_FORBIDDEN
    if effects.pushes_remotely or effects.accesses_network or effects.accesses_paths_outside_repository:
        return ApprovalClass.ADMINISTRATOR_POLICY_REQUIRED
    if effects.creates_commits or effects.modifies_git_index:
        return ApprovalClass.HUMAN_CONFIRMATION_REQUIRED
    if effects.runs_local_processes or effects.invokes_model:
        return ApprovalClass.PER_INVOCATION_APPROVAL_REQUIRED
    if any(
        (
            effects.writes_repository_files,
            effects.writes_infctx,
            effects.mutates_plans,
            effects.mutates_task_status,
            effects.creates_artifacts,
        )
    ):
        return ApprovalClass.PER_TASK_APPROVAL_REQUIRED
    if effects.read_only:
        return ApprovalClass.POLICY_PREAPPROVED_READ_ONLY
    return ApprovalClass.NO_EXECUTION_AVAILABLE
