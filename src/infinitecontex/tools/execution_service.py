"""Application orchestration for direct-human and task-bound G2 repository reads."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from infinitecontex.planning.models import Capability, PlanRevision, Task
from infinitecontex.planning.store import PlanStore
from infinitecontex.task_context.models import PathReferenceKind, RepositoryInventory, TaskContextDecision
from infinitecontex.task_context.paths import PathResolver, repository_glob_match
from infinitecontex.task_context.repository import GitRepositoryState, GitStateProvider, RepositoryInventoryService
from infinitecontex.task_context.store import TaskContextAnalysisStore
from infinitecontex.tools.execution_errors import TaskBoundAdmissionError
from infinitecontex.tools.execution_fingerprints import grant_fingerprint, invocation_fingerprint
from infinitecontex.tools.execution_gateway import ReadOnlyExecutionGateway
from infinitecontex.tools.execution_models import (
    AuthorizationSource,
    CallerType,
    ExecutionEnvelope,
    InvocationInput,
    OperationKind,
    ReadOnlyExecutionGrant,
    ToolInvocation,
)
from infinitecontex.tools.fingerprints import sha256_payload
from infinitecontex.tools.models import PolicyDecision
from infinitecontex.tools.policy import ToolPolicy
from infinitecontex.tools.registry import ToolRegistry
from infinitecontex.tools.sensitive import SensitivePathPolicy

_PASSING_CONTEXT = {
    TaskContextDecision.FITS_TARGET,
    TaskContextDecision.FITS_WITH_WARNING,
    TaskContextDecision.FITS_HARD_LIMIT,
}


class NoCommandGitStateProvider(GitStateProvider):
    """G2 never invokes Git or a subprocess while constructing its inventory."""

    def inspect(self, root: Path) -> GitRepositoryState:
        return GitRepositoryState(available=False)


class RepositoryReadExecutionService:
    def __init__(
        self,
        registry: ToolRegistry,
        gateway: ReadOnlyExecutionGateway,
        *,
        inventory_service: RepositoryInventoryService | None = None,
        plan_store: PlanStore | None = None,
        analysis_store: TaskContextAnalysisStore | None = None,
        tool_policy: ToolPolicy | None = None,
        sensitive_policy: SensitivePathPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry
        self.gateway = gateway
        self.sensitive_policy = sensitive_policy or SensitivePathPolicy()
        self.inventory_service = inventory_service or RepositoryInventoryService(
            git_provider=NoCommandGitStateProvider(),
            additional_path_filter=self.sensitive_policy.permits,
        )
        self.plan_store = plan_store
        self.analysis_store = analysis_store
        self.tool_policy = tool_policy or ToolPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))

    def execute_human(
        self,
        repository_root: Path,
        tool_name: str,
        inputs: InvocationInput,
        *,
        correlation_id: str | None = None,
    ) -> ExecutionEnvelope:
        root = repository_root.resolve(strict=True)
        inventory = self.inventory_service.build(root)
        normalized = self._normalize(inputs, root, inventory)
        definition = self.registry.get_by_name_version(tool_name, "1.0.0")
        invocation = self._invocation(
            definition.tool_id,
            definition.tool_version,
            inventory,
            normalized,
            CallerType.HUMAN_CLI,
            AuthorizationSource.EXPLICIT_HUMAN_CLI,
            correlation_id=correlation_id,
        )
        grant = self._grant(invocation, definition.definition_fingerprint)
        return self.gateway.execute(invocation, grant, inventory, root)

    def execute_task_read(
        self,
        repository_root: Path,
        plan_id: str,
        task_id: str,
        inputs: InvocationInput,
        *,
        revision: int | None = None,
        correlation_id: str | None = None,
    ) -> ExecutionEnvelope:
        if self.plan_store is None or self.analysis_store is None:
            raise TaskBoundAdmissionError("Task-bound execution stores are unavailable")
        plan = self.plan_store.load_current(plan_id)
        if revision is not None and revision != plan.current_revision:
            raise TaskBoundAdmissionError("Requested revision is not the exact current plan revision")
        task = self._task(plan, task_id)
        if Capability.READ_REPOSITORY not in task.requested_capabilities:
            raise TaskBoundAdmissionError("Task did not request the repository-read capability")
        analysis = self.analysis_store.load_current(plan.plan_id, plan.current_revision, task.task_id)
        if analysis.task_fingerprint != task.task_fingerprint or analysis.graph_fingerprint != plan.graph_fingerprint:
            raise TaskBoundAdmissionError("Task-context analysis does not match the exact task and plan")
        if analysis.decision not in _PASSING_CONTEXT:
            raise TaskBoundAdmissionError("Task-context analysis is not passing")
        root = repository_root.resolve(strict=True)
        inventory = self.inventory_service.build(root)
        if inventory.snapshot.semantic_fingerprint != analysis.repository_snapshot_fingerprint:
            raise TaskBoundAdmissionError("Task-context analysis is stale for the current repository snapshot")
        normalized = self._normalize(inputs, root, inventory)
        self._validate_task_scope(task, normalized)
        tool_name = (
            "repository.read-source-range"
            if normalized.operation == OperationKind.READ_RANGE
            else "repository.read-file"
        )
        definition = self.registry.get_by_name_version(tool_name, "1.0.0")
        decision = self.tool_policy.evaluate(
            plan,
            task,
            definition,
            registry_fingerprint=self.registry.fingerprint,
            analysis=analysis,
            analysis_stale=False,
        )
        if not decision.structurally_eligible or decision.decision not in {
            PolicyDecision.ELIGIBLE_FOR_FUTURE_REQUEST,
            PolicyDecision.ELIGIBLE_WITH_HUMAN_APPROVAL,
        }:
            raise TaskBoundAdmissionError(f"G1 structural policy denied the read: {decision.decision.value}")
        invocation = self._invocation(
            definition.tool_id,
            definition.tool_version,
            inventory,
            normalized,
            CallerType.TASK_BOUND,
            AuthorizationSource.TASK_POLICY,
            plan=plan,
            task=task,
            correlation_id=correlation_id,
        )
        grant = self._grant(invocation, definition.definition_fingerprint)
        return self.gateway.execute(invocation, grant, inventory, root)

    def _normalize(self, inputs: InvocationInput, root: Path, inventory: RepositoryInventory) -> InvocationInput:
        resolver = PathResolver(root, inventory)
        updates: dict[str, object] = {}
        for field, kind in (
            ("path", PathReferenceKind.EXACT_FILE),
            ("glob", PathReferenceKind.GLOB),
            ("directory_prefix", PathReferenceKind.EXACT_DIRECTORY),
        ):
            value = getattr(inputs, field)
            if value is None:
                continue
            normalized, error = resolver.normalize(value, kind)
            if error or normalized is None:
                raise TaskBoundAdmissionError(error or f"Invalid {field}")
            updates[field] = normalized
        return inputs.model_copy(update=updates)

    def _invocation(
        self,
        tool_id: str,
        version: str,
        inventory: RepositoryInventory,
        inputs: InvocationInput,
        caller: CallerType,
        authorization: AuthorizationSource,
        *,
        plan: PlanRevision | None = None,
        task: Task | None = None,
        correlation_id: str | None = None,
    ) -> ToolInvocation:
        scopes = self._scopes(inputs)
        correlation_payload = {
            "input": inputs.model_dump(mode="json"),
            "snapshot": inventory.snapshot.semantic_fingerprint,
        }
        correlation = correlation_id or f"repo-read-{sha256_payload(correlation_payload)[:24]}"
        provisional = ToolInvocation(
            invocation_id="tool-invocation-" + "0" * 24,
            invocation_fingerprint="0" * 64,
            correlation_id=correlation,
            tool_id=tool_id,
            tool_version=version,
            registry_fingerprint=self.registry.fingerprint,
            repository_snapshot_fingerprint=inventory.snapshot.semantic_fingerprint,
            plan_id=plan.plan_id if plan else None,
            plan_revision=plan.current_revision if plan else None,
            task_id=task.task_id if task else None,
            task_fingerprint=task.task_fingerprint if task else None,
            normalized_input=inputs,
            requested_scopes=scopes,
            caller_type=caller,
            authorization_source=authorization,
            created_at=self.clock(),
        )
        fingerprint = invocation_fingerprint(provisional)
        return provisional.model_copy(
            update={"invocation_id": f"tool-invocation-{fingerprint[:24]}", "invocation_fingerprint": fingerprint}
        )

    def _grant(self, invocation: ToolInvocation, tool_fingerprint: str) -> ReadOnlyExecutionGrant:
        provisional = ReadOnlyExecutionGrant(
            grant_id="read-grant-" + "0" * 24,
            grant_fingerprint="0" * 64,
            invocation_id=invocation.invocation_id,
            invocation_fingerprint=invocation.invocation_fingerprint,
            tool_id=invocation.tool_id,
            tool_fingerprint=tool_fingerprint,
            repository_snapshot_fingerprint=invocation.repository_snapshot_fingerprint,
            authorized_scopes=invocation.requested_scopes,
            authorization_source=invocation.authorization_source,
            created_at=self.clock(),
        )
        fingerprint = grant_fingerprint(provisional)
        return provisional.model_copy(
            update={"grant_id": f"read-grant-{fingerprint[:24]}", "grant_fingerprint": fingerprint}
        )

    @staticmethod
    def _scopes(inputs: InvocationInput) -> tuple[str, ...]:
        values = [value for value in (inputs.path, inputs.glob, inputs.directory_prefix) if value]
        if inputs.query:
            values.append(f"literal-query-sha256:{sha256_payload(inputs.query)}")
        if not values:
            values.append("inventory-only")
        return tuple(sorted(values))

    @staticmethod
    def _task(plan: PlanRevision, task_id: str) -> Task:
        task = next((item for item in plan.tasks if item.task_id == task_id), None)
        if task is None:
            raise TaskBoundAdmissionError(f"Task {task_id} does not belong to plan {plan.plan_id}")
        return task

    @staticmethod
    def _validate_task_scope(task: Task, inputs: InvocationInput) -> None:
        if inputs.path is None:
            raise TaskBoundAdmissionError("G2 task-bound execution supports explicit file reads only")
        allowed = tuple((*task.affected_scopes, *task.context_requirements.required_files))
        if not allowed or not any(
            repository_glob_match(inputs.path, scope.replace("\\", "/")) or inputs.path == scope.replace("\\", "/")
            for scope in allowed
        ):
            raise TaskBoundAdmissionError("Requested path is outside the task-declared read and affected scopes")
        if any(
            repository_glob_match(inputs.path, scope.replace("\\", "/")) or inputs.path == scope.replace("\\", "/")
            for scope in task.forbidden_scopes
        ):
            raise TaskBoundAdmissionError("Requested path overlaps a forbidden task scope")
