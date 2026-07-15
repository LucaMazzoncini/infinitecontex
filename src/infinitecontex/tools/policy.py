"""Versioned deterministic policy for one persisted task/tool pair."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from infinitecontex.planning.models import PlanRevision, PlanStatus, Task, TaskStatus
from infinitecontex.task_context.models import TaskContextAnalysis, TaskContextDecision
from infinitecontex.tools.capabilities import capability_gaps, normalize_capabilities
from infinitecontex.tools.fingerprints import decision_fingerprint
from infinitecontex.tools.models import (
    ApprovalClass,
    AvailabilityState,
    ImplementationStatus,
    PolicyDecision,
    ToolDefinition,
    ToolPolicyDecision,
)
from infinitecontex.tools.risk import derive_approval_class, derive_minimum_risk
from infinitecontex.tools.scopes import evaluate_tool_scopes
from infinitecontex.tools.validation import validate_tool_definition

_FITTING = {
    TaskContextDecision.FITS_TARGET,
    TaskContextDecision.FITS_WITH_WARNING,
    TaskContextDecision.FITS_HARD_LIMIT,
}
_READY = {TaskStatus.READY, TaskStatus.IN_PROGRESS, TaskStatus.AWAITING_REVIEW}


class ToolPolicy:
    policy_id = "deterministic-tool-policy-v1"
    version = 1
    risk_derivation_version = 1

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(UTC))

    def evaluate(
        self,
        plan: PlanRevision,
        task: Task,
        definition: ToolDefinition,
        *,
        registry_fingerprint: str,
        analysis: TaskContextAnalysis | None = None,
        analysis_stale: bool = False,
        analysis_stale_reasons: tuple[str, ...] = (),
        plan_approved: bool = False,
    ) -> ToolPolicyDecision:
        validate_tool_definition(definition)
        requested = normalize_capabilities(task.requested_capabilities)
        required = normalize_capabilities(definition.required_capabilities)
        missing_requested, missing_granted = capability_gaps(requested, required)
        scopes = evaluate_tool_scopes(task, definition, analysis)
        risk = derive_minimum_risk(definition.effects)
        approval = derive_approval_class(definition)
        hard: list[tuple[PolicyDecision, str, str, str]] = []
        warnings: list[str] = []

        if task.plan_id != plan.plan_id or task not in plan.tasks:
            hard.append(
                (
                    PolicyDecision.INVALID_REQUEST,
                    "task_plan_mismatch",
                    "Task does not belong to the exact plan revision.",
                    "Select a task from the requested persisted revision.",
                )
            )
        if plan.status in {PlanStatus.CANCELLED, PlanStatus.COMPLETED, PlanStatus.SUPERSEDED}:
            hard.append(
                (
                    PolicyDecision.DENIED_POLICY,
                    "plan_not_active",
                    f"Plan status {plan.status.value} is not eligible.",
                    "Select a draft or active plan revision.",
                )
            )
        if definition.availability == AvailabilityState.PERMANENTLY_FORBIDDEN:
            hard.append(
                (
                    PolicyDecision.PERMANENTLY_FORBIDDEN,
                    "permanently_forbidden",
                    "Policy permanently forbids the declared effects.",
                    "Choose a non-destructive registered tool.",
                )
            )
        elif definition.implementation_status == ImplementationStatus.DEPRECATED:
            hard.append(
                (
                    PolicyDecision.DENIED_TOOL_DEPRECATED,
                    "tool_deprecated",
                    "The tool definition is deprecated.",
                    "Use the declared replacement tool.",
                )
            )
        elif definition.availability in {AvailabilityState.UNAVAILABLE, AvailabilityState.DISABLED} or (
            definition.implementation_status == ImplementationStatus.UNAVAILABLE
        ):
            hard.append(
                (
                    PolicyDecision.DENIED_TOOL_UNAVAILABLE,
                    "tool_unavailable",
                    "The tool definition is unavailable.",
                    "Choose an available data-only definition.",
                )
            )
        if task.status not in _READY:
            hard.append(
                (
                    PolicyDecision.DENIED_TASK_NOT_READY,
                    "task_not_ready",
                    f"Task status {task.status.value} is not ready for a future request.",
                    "Move the task through an explicit valid status transition.",
                )
            )

        needs_context = any(scope.requires_resolved_task_context for scope in definition.scopes)
        if needs_context and analysis is None:
            hard.append(
                (
                    PolicyDecision.DENIED_UNRESOLVED_CONTEXT,
                    "task_context_missing",
                    "The tool requires a persisted task-context analysis.",
                    "Run `infctx plan context-fit` for this task and exact model digest.",
                )
            )
        elif analysis is not None:
            identity_mismatch = any(
                (
                    analysis.plan_id != plan.plan_id,
                    analysis.plan_revision != plan.current_revision,
                    analysis.task_id != task.task_id,
                    analysis.task_fingerprint != task.task_fingerprint,
                    analysis.graph_fingerprint != plan.graph_fingerprint,
                )
            )
            if identity_mismatch:
                hard.append(
                    (
                        PolicyDecision.DENIED_STALE_ANALYSIS,
                        "analysis_identity_mismatch",
                        "Task-context linkage does not match the selected task and revision.",
                        "Regenerate task context for the current revision.",
                    )
                )
            if analysis_stale:
                hard.append(
                    (
                        PolicyDecision.DENIED_STALE_ANALYSIS,
                        "analysis_stale",
                        "Task-context analysis is stale.",
                        "Regenerate task context after repository or profile changes.",
                    )
                )
            if analysis.decision == TaskContextDecision.SPLIT_REQUIRED:
                hard.append(
                    (
                        PolicyDecision.DENIED_TASK_OVERSIZED,
                        "task_oversized",
                        "Task context does not fit the exact profile.",
                        "Create and explicitly approve a deterministic split proposal.",
                    )
                )
            elif needs_context and analysis.decision not in _FITTING:
                hard.append(
                    (
                        PolicyDecision.DENIED_UNRESOLVED_CONTEXT,
                        "context_not_passing",
                        f"Task context decision is {analysis.decision.value}.",
                        "Resolve required context and regenerate the analysis.",
                    )
                )
        if missing_requested:
            hard.append(
                (
                    PolicyDecision.DENIED_CAPABILITY_NOT_REQUESTED,
                    "capability_not_requested",
                    "The task did not request every capability required by the tool.",
                    "Revise the plan explicitly; a tool check cannot grant capabilities.",
                )
            )
        failed_scopes = tuple(check for check in scopes if not check.passed)
        if failed_scopes:
            scope_decision = (
                PolicyDecision.DENIED_FORBIDDEN_SCOPE
                if any(check.reason_code == "forbidden_scope_overlap" for check in failed_scopes)
                else PolicyDecision.DENIED_SCOPE_MISMATCH
            )
            hard.append(
                (
                    scope_decision,
                    failed_scopes[0].reason_code,
                    failed_scopes[0].detail,
                    "Narrow and resolve affected/forbidden scopes, then regenerate task context.",
                )
            )
        mutation = not definition.effects.read_only and any(
            value
            for name, value in definition.effects
            if name not in {"read_only", "reads_repository", "requests_human_approval"}
        )
        if mutation and not plan_approved:
            hard.append(
                (
                    PolicyDecision.DENIED_UNAPPROVED_PLAN,
                    "plan_not_approved",
                    "No exact approval state exists for this plan revision.",
                    "A future execution milestone must obtain explicit approval; G1 cannot approve it.",
                )
            )
        if missing_granted:
            warnings.append("Required capabilities are not granted; G1 grants none.")
            if definition.implementation_status == ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION:
                hard.append(
                    (
                        PolicyDecision.DENIED_CAPABILITY_NOT_GRANTED,
                        "capability_not_granted",
                        "The tool requires capabilities that no G1 authority can grant.",
                        "Wait for a later explicit grant and execution-admission milestone.",
                    )
                )
        warnings.extend(analysis_stale_reasons)

        if hard:
            decision, _, explanation, _ = hard[0]
            eligible = False
            approval = (
                ApprovalClass.PERMANENTLY_FORBIDDEN
                if decision == PolicyDecision.PERMANENTLY_FORBIDDEN
                else ApprovalClass.DENIED
            )
        elif approval == ApprovalClass.POLICY_PREAPPROVED_READ_ONLY:
            decision = PolicyDecision.ELIGIBLE_FOR_FUTURE_REQUEST
            explanation = (
                "The definition is structurally eligible for a future read-only request; execution is unavailable."
            )
            eligible = True
        else:
            decision = PolicyDecision.ELIGIBLE_WITH_HUMAN_APPROVAL
            explanation = "The definition is structurally eligible only for a future approval and grant workflow."
            eligible = True
        context_state = (
            "not_required"
            if analysis is None and not needs_context
            else "missing"
            if analysis is None
            else "stale"
            if analysis_stale
            else analysis.decision.value
        )
        reasons = tuple(dict.fromkeys(item[1] for item in hard)) or ("structurally_eligible",)
        remediation = tuple(dict.fromkeys(item[3] for item in hard)) or (
            "Wait for a later execution gateway; G1 performs inspection only.",
        )
        provisional = ToolPolicyDecision(
            decision_id="tool-decision-" + "0" * 24,
            semantic_fingerprint="0" * 64,
            registry_fingerprint=registry_fingerprint,
            plan_id=plan.plan_id,
            plan_revision=plan.current_revision,
            plan_revision_fingerprint=plan.revision_fingerprint,
            graph_fingerprint=plan.graph_fingerprint,
            task_id=task.task_id,
            task_fingerprint=task.task_fingerprint,
            task_status=task.status.value,
            tool_id=definition.tool_id,
            tool_fingerprint=definition.definition_fingerprint,
            repository_snapshot_fingerprint=analysis.repository_snapshot_fingerprint if analysis else None,
            task_context_analysis_fingerprint=analysis.semantic_fingerprint if analysis else None,
            context_fit_state=context_state,
            requested_capabilities=requested,
            required_capabilities=required,
            granted_capabilities=(),
            missing_requested_capabilities=missing_requested,
            missing_granted_capabilities=missing_granted,
            scope_checks=scopes,
            risk_level=risk,
            approval_class=approval,
            decision=decision,
            structurally_eligible=eligible,
            executable_now=False,
            warnings=tuple(sorted(set(warnings))),
            reason_codes=reasons,
            explanation=explanation,
            remediation=remediation,
            created_at=self.clock(),
        )
        fingerprint = decision_fingerprint(provisional)
        return provisional.model_copy(
            update={
                "decision_id": f"tool-decision-{fingerprint[:24]}",
                "semantic_fingerprint": fingerprint,
            }
        )
