"""Task-bound proposal, approval, and execution orchestration for G4."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from infinitecontex.events.logger import EventLogger
from infinitecontex.planning.models import Capability, TaskStatus
from infinitecontex.planning.store import PlanStore
from infinitecontex.task_context.models import RepositoryInventory, TaskContextDecision
from infinitecontex.task_context.paths import repository_glob_match
from infinitecontex.task_context.repository import RepositoryInventoryService
from infinitecontex.task_context.store import TaskContextAnalysisStore
from infinitecontex.tools.execution_service import NoCommandGitStateProvider
from infinitecontex.tools.models import PolicyDecision
from infinitecontex.tools.policy import ToolPolicy
from infinitecontex.tools.registry import ToolRegistry
from infinitecontex.tools.sensitive import SensitivePathPolicy
from infinitecontex.tools.validation_definitions import (
    ValidationCommandRegistry,
    approval_fingerprint,
    build_arguments,
    build_environment,
    evidence_fingerprint,
    fingerprint_payload,
    proposal_fingerprint,
    record_fingerprint,
    resolve_executable,
    verify_executable,
)
from infinitecontex.tools.validation_models import (
    CommandCategory,
    ExitClassification,
    ValidationApproval,
    ValidationAuthorization,
    ValidationDecision,
    ValidationEvidence,
    ValidationExecutionRecord,
    ValidationProposal,
)
from infinitecontex.tools.validation_runner import BoundedProcessRunner
from infinitecontex.tools.validation_store import ValidationStore

_PASSING = {TaskContextDecision.FITS_TARGET, TaskContextDecision.FITS_WITH_WARNING, TaskContextDecision.FITS_HARD_LIMIT}
_ELIGIBLE = {TaskStatus.READY, TaskStatus.IN_PROGRESS, TaskStatus.AWAITING_REVIEW}
_CAPABILITY = {
    CommandCategory.TEST: Capability.RUN_TESTS,
    CommandCategory.BUILD: Capability.RUN_BUILD,
    CommandCategory.FORMAT: Capability.EXECUTE_COMMANDS,
    CommandCategory.LINT: Capability.EXECUTE_COMMANDS,
    CommandCategory.STATIC_ANALYSIS: Capability.EXECUTE_COMMANDS,
}


class ValidationExecutionService:
    def __init__(
        self,
        registry: ToolRegistry,
        commands: ValidationCommandRegistry,
        plan_store: PlanStore,
        analysis_store: TaskContextAnalysisStore,
        store: ValidationStore,
        *,
        inventory_service: RepositoryInventoryService | None = None,
        runner: BoundedProcessRunner | None = None,
        policy: ToolPolicy | None = None,
        event_logger: EventLogger | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry, self.commands = registry, commands
        self.plan_store, self.analysis_store, self.store = plan_store, analysis_store, store
        self.inventory = inventory_service or RepositoryInventoryService(
            git_provider=NoCommandGitStateProvider(), additional_path_filter=SensitivePathPolicy().permits
        )
        self.runner, self.policy = runner or BoundedProcessRunner(), policy or ToolPolicy()
        self.event_logger, self.clock = event_logger, clock or (lambda: datetime.now(UTC))
        self.bindings = {
            item.tool_id: item.definition_fingerprint
            for item in (
                registry.get_by_name_version(name, "1.0.0")
                for name in ("execution.run-tests", "execution.run-build", "execution.run-static-analysis")
            )
        }

    def propose(
        self,
        root: Path,
        plan_id: str,
        task_id: str,
        command: str,
        parameters: Mapping[str, tuple[str, ...]] | None = None,
        *,
        revision: int | None = None,
    ) -> ValidationProposal:
        self._event("validation_proposal_started", {"plan_id": plan_id, "task_id": task_id})
        plan = self.plan_store.load_current(plan_id)
        if revision is not None and plan.current_revision != revision:
            raise ValueError("Requested plan revision is not current")
        task = next((item for item in plan.tasks if item.task_id == task_id), None)
        if task is None or task.status not in _ELIGIBLE:
            raise ValueError("Task is missing or not eligible for validation")
        analysis = self.analysis_store.load_current(plan_id, plan.current_revision, task_id)
        if analysis.decision not in _PASSING or analysis.task_fingerprint != task.task_fingerprint:
            raise ValueError("Task-context analysis is stale or does not fit")
        root = root.resolve(strict=True)
        before = self.inventory.build(root)
        if analysis.repository_snapshot_fingerprint != before.snapshot.semantic_fingerprint:
            raise ValueError("Task-context analysis is stale for the repository")
        definition = self.commands.get(command)
        capability = _CAPABILITY[definition.category]
        if capability not in task.requested_capabilities:
            raise ValueError(f"Task did not request capability {capability.value}")
        tool = self.registry.get_by_name_version(definition.tool_name, "1.0.0")
        if self.bindings.get(tool.tool_id) != tool.definition_fingerprint:
            raise ValueError("Exact validation handler binding is missing or stale")
        decision = self.policy.evaluate(
            plan,
            task,
            tool,
            registry_fingerprint=self.registry.fingerprint,
            analysis=analysis,
            authorization_workflow=True,
        )
        if not decision.structurally_eligible or decision.decision != PolicyDecision.ELIGIBLE_WITH_HUMAN_APPROVAL:
            raise ValueError(f"Tool policy denied validation: {decision.decision.value}")
        executable = resolve_executable()
        environment, summary = build_environment(os.environ)
        del environment
        arguments, normalized = build_arguments(definition, parameters or {}, root, before)
        for parameter_name, parameter_values in normalized:
            if parameter_name == "target":
                for target_value in parameter_values:
                    if task.forbidden_scopes and any(_scope(target_value, scope) for scope in task.forbidden_scopes):
                        raise ValueError(f"Target {target_value} overlaps a forbidden scope")
        args_fp = fingerprint_payload(arguments)
        provisional = ValidationProposal(
            proposal_id="validation-proposal-" + "0" * 24,
            semantic_fingerprint="0" * 64,
            plan_id=plan.plan_id,
            plan_revision=plan.current_revision,
            graph_fingerprint=plan.graph_fingerprint,
            task_id=task.task_id,
            task_fingerprint=task.task_fingerprint,
            repository_snapshot_fingerprint=before.snapshot.semantic_fingerprint,
            analysis_id=analysis.analysis_id,
            analysis_fingerprint=analysis.semantic_fingerprint,
            policy_decision_id=decision.decision_id,
            policy_decision_fingerprint=decision.semantic_fingerprint,
            tool_id=tool.tool_id,
            tool_version=tool.tool_version,
            tool_fingerprint=tool.definition_fingerprint,
            command_id=definition.command_id,
            command_version=definition.version,
            command_fingerprint=definition.fingerprint,
            executable=executable,
            arguments=arguments,
            parameters=normalized,
            arguments_fingerprint=args_fp,
            working_directory=".",
            environment=summary,
            timeout_seconds=definition.timeout_seconds,
            termination_grace_seconds=definition.termination_grace_seconds,
            stdout_limit_bytes=definition.stdout_limit_bytes,
            stderr_limit_bytes=definition.stderr_limit_bytes,
            output_line_limit=definition.output_line_limit,
            repository_mutation_policy=definition.repository_mutation_policy,
            accepted_exit_codes=definition.accepted_exit_codes,
            requested_capability=capability.value,
            risk=tool.derived_risk.value,
            validation_passed=True,
            created_at=self.clock(),
        )
        fp = proposal_fingerprint(provisional)
        value = provisional.model_copy(
            update={"semantic_fingerprint": fp, "proposal_id": f"validation-proposal-{fp[:24]}"}
        )
        self.store.save_proposal(value)
        self._event("validation_proposal_persisted", {"proposal_id": value.proposal_id})
        return value

    def decide(
        self,
        plan_id: str,
        proposal_id: str,
        actor: str,
        decision: ValidationDecision,
        *,
        reason: str | None = None,
        warnings_acknowledged: bool = False,
    ) -> ValidationApproval:
        proposal = self.store.load_proposal(plan_id, proposal_id)
        provisional = ValidationApproval(
            approval_id="validation-approval-" + "0" * 24,
            approval_fingerprint="0" * 64,
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.semantic_fingerprint,
            plan_id=plan_id,
            plan_revision=proposal.plan_revision,
            task_fingerprint=proposal.task_fingerprint,
            repository_snapshot_fingerprint=proposal.repository_snapshot_fingerprint,
            command_fingerprint=proposal.command_fingerprint,
            actor=actor,
            decision=decision,
            reason=reason,
            warnings_acknowledged=warnings_acknowledged,
            decided_at=self.clock(),
        )
        fp = approval_fingerprint(provisional)
        value = provisional.model_copy(
            update={"approval_fingerprint": fp, "approval_id": f"validation-approval-{fp[:24]}"}
        )
        self.store.save_approval(value)
        self._event("validation_approval_recorded", {"proposal_id": proposal_id, "decision": decision.value})
        return value

    def run(
        self, root: Path, plan_id: str, proposal_id: str, *, criterion_id: str | None = None
    ) -> ValidationExecutionRecord:
        self._event("validation_execution_admission_started", {"proposal_id": proposal_id})
        proposal = self.store.load_proposal(plan_id, proposal_id)
        approvals = self.store.list_approvals(plan_id, proposal_id)
        if len(approvals) != 1 or approvals[0].decision != ValidationDecision.APPROVED:
            raise ValueError("An exact immutable human approval is required")
        approval = approvals[0]
        plan = self.plan_store.load_current(plan_id)
        task = next((item for item in plan.tasks if item.task_id == proposal.task_id), None)
        if (
            plan.current_revision != proposal.plan_revision
            or plan.graph_fingerprint != proposal.graph_fingerprint
            or task is None
            or task.task_fingerprint != proposal.task_fingerprint
            or task.status not in _ELIGIBLE
        ):
            raise ValueError("Plan or task changed after validation proposal")
        analysis = self.analysis_store.load_current(plan_id, plan.current_revision, task.task_id)
        if analysis.semantic_fingerprint != proposal.analysis_fingerprint or analysis.decision not in _PASSING:
            raise ValueError("Task-context analysis changed after proposal")
        definition = self.commands.get(proposal.command_id)
        if definition.fingerprint != proposal.command_fingerprint:
            raise ValueError("Command definition changed after proposal")
        tool = self.registry.get(proposal.tool_id)
        if (
            tool.definition_fingerprint != proposal.tool_fingerprint
            or self.bindings.get(tool.tool_id) != tool.definition_fingerprint
        ):
            raise ValueError("Tool definition or handler binding changed")
        verify_executable(proposal.executable)
        root = root.resolve(strict=True)
        before = self.inventory.build(root)
        if before.snapshot.semantic_fingerprint != proposal.repository_snapshot_fingerprint:
            raise ValueError("Repository changed after proposal")
        environment, summary = build_environment(os.environ)
        if summary.fingerprint != proposal.environment.fingerprint:
            raise ValueError("Sanitized environment changed after proposal")
        authorization_payload = {
            "proposal_id": proposal.proposal_id,
            "proposal_fingerprint": proposal.semantic_fingerprint,
            "approval_id": approval.approval_id,
            "approval_fingerprint": approval.approval_fingerprint,
            "plan_id": plan_id,
            "plan_revision": proposal.plan_revision,
            "task_id": task.task_id,
            "task_fingerprint": task.task_fingerprint,
            "repository_snapshot_fingerprint": before.snapshot.semantic_fingerprint,
            "tool_fingerprint": tool.definition_fingerprint,
            "command_fingerprint": definition.fingerprint,
            "executable_sha256": proposal.executable.sha256,
            "arguments_fingerprint": proposal.arguments_fingerprint,
            "working_directory": ".",
            "environment_fingerprint": summary.fingerprint,
            "timeout_seconds": proposal.timeout_seconds,
            "stdout_limit_bytes": proposal.stdout_limit_bytes,
            "stderr_limit_bytes": proposal.stderr_limit_bytes,
        }
        authorization_fp = fingerprint_payload(authorization_payload)
        ValidationAuthorization.model_validate(
            {
                "authorization_id": f"validation-authorization-{authorization_fp[:24]}",
                "fingerprint": authorization_fp,
                **authorization_payload,
            }
        )
        started = self.clock()
        self._event("validation_process_started", {"proposal_id": proposal_id, "command_id": proposal.command_id})
        outcome = self.runner.run(
            proposal.executable.resolved_path,
            proposal.arguments,
            cwd=root,
            environment=environment,
            timeout_seconds=proposal.timeout_seconds,
            grace_seconds=proposal.termination_grace_seconds,
            stdout_limit=proposal.stdout_limit_bytes,
            stderr_limit=proposal.stderr_limit_bytes,
            line_limit=proposal.output_line_limit,
        )
        after = self.inventory.build(root)
        changed = _changed_paths(before, after, definition.permitted_transient_prefixes)
        if changed:
            classification = ExitClassification.REPOSITORY_MUTATION_DETECTED
        elif outcome.timed_out:
            classification = ExitClassification.TIMED_OUT
        elif outcome.output_limit_exceeded:
            classification = ExitClassification.OUTPUT_LIMIT_EXCEEDED
        elif outcome.exit_code in proposal.accepted_exit_codes:
            classification = ExitClassification.PASSED
        elif outcome.exit_code is not None:
            classification = ExitClassification.VALIDATION_FAILED
        else:
            classification = ExitClassification.PROCESS_ERROR
        completed = self.clock()
        provisional = ValidationExecutionRecord(
            execution_id="tool-validation-" + "0" * 24,
            record_fingerprint="0" * 64,
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.semantic_fingerprint,
            approval_id=approval.approval_id,
            approval_fingerprint=approval.approval_fingerprint,
            plan_id=plan_id,
            plan_revision=proposal.plan_revision,
            task_id=task.task_id,
            task_fingerprint=task.task_fingerprint,
            tool_id=tool.tool_id,
            tool_fingerprint=tool.definition_fingerprint,
            command_id=definition.command_id,
            command_fingerprint=definition.fingerprint,
            executable_sha256=proposal.executable.sha256,
            arguments_fingerprint=proposal.arguments_fingerprint,
            environment_fingerprint=summary.fingerprint,
            repository_snapshot_before=before.snapshot.semantic_fingerprint,
            repository_snapshot_after=after.snapshot.semantic_fingerprint,
            started_at=started,
            completed_at=completed,
            classification=classification,
            exit_code=outcome.exit_code,
            timed_out=outcome.timed_out,
            cancelled=outcome.cancelled,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            unexpected_changed_paths=changed,
            warning_codes=("output_redacted",) if outcome.stdout.redacted or outcome.stderr.redacted else (),
            error_codes=(classification.value,) if classification != ExitClassification.PASSED else (),
        )
        fp = record_fingerprint(provisional)
        record = provisional.model_copy(update={"record_fingerprint": fp, "execution_id": f"tool-validation-{fp[:24]}"})
        self.store.save_record(record)
        if classification == ExitClassification.PASSED:
            preview = (outcome.stdout.preview + outcome.stderr.preview)[:65536]
            evidence = ValidationEvidence(
                evidence_id="validation-evidence-" + "0" * 24,
                fingerprint="0" * 64,
                execution_id=record.execution_id,
                plan_id=plan_id,
                task_id=task.task_id,
                criterion_id=criterion_id,
                command_id=definition.command_id,
                repository_snapshot_before=record.repository_snapshot_before,
                repository_snapshot_after=record.repository_snapshot_after,
                classification=classification,
                stdout_sha256=outcome.stdout.sha256,
                stderr_sha256=outcome.stderr.sha256,
                preview=preview,
                created_at=completed,
            )
            evidence_fp = evidence_fingerprint(evidence)
            self.store.save_evidence(
                evidence.model_copy(
                    update={"fingerprint": evidence_fp, "evidence_id": f"validation-evidence-{evidence_fp[:24]}"}
                )
            )
        self._event(
            "validation_execution_completed",
            {"execution_id": record.execution_id, "classification": classification.value},
        )
        return record

    def _event(self, name: str, payload: dict[str, object]) -> None:
        if self.event_logger:
            self.event_logger.log(name, payload)


def _scope(path: str, scope: str) -> bool:
    normalized = scope.replace("\\", "/").rstrip("/")
    return path == normalized or repository_glob_match(path, normalized) or path.startswith(normalized + "/")


def _changed_paths(
    before: RepositoryInventory, after: RepositoryInventory, transients: tuple[str, ...]
) -> tuple[str, ...]:
    left = {item.path: (item.content_hash, item.classification) for item in before.entries}
    right = {item.path: (item.content_hash, item.classification) for item in after.entries}
    changed = sorted(path for path in set(left) | set(right) if left.get(path) != right.get(path))
    return tuple(
        path
        for path in changed
        if not any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in transients)
    )
