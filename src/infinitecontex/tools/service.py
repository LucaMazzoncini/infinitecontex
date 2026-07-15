"""Offline orchestration for registry inspection and task/tool policy checks."""

from __future__ import annotations

from pathlib import Path

from infinitecontex.model_profiles.errors import ModelProfileError
from infinitecontex.model_profiles.store import ModelProfileStore
from infinitecontex.planning.models import PlanRevision, Task
from infinitecontex.planning.store import PlanStore
from infinitecontex.task_context.errors import TaskContextNotFoundError
from infinitecontex.task_context.models import TaskContextAnalysis
from infinitecontex.task_context.repository import RepositoryInventoryService
from infinitecontex.task_context.store import TaskContextAnalysisStore
from infinitecontex.task_splitting.models import ApplicationStatus, ApprovalDecision
from infinitecontex.task_splitting.store import TaskSplitStore
from infinitecontex.tools.builtins import builtin_registry
from infinitecontex.tools.errors import ToolTaskMissingError
from infinitecontex.tools.models import DecisionStaleness, ToolPolicyDecision
from infinitecontex.tools.policy import ToolPolicy
from infinitecontex.tools.registry import ToolRegistry
from infinitecontex.tools.store import ToolDecisionStore


class ToolInspectionService:
    def __init__(
        self,
        plan_store: PlanStore,
        analysis_store: TaskContextAnalysisStore,
        decision_store: ToolDecisionStore,
        *,
        inventory_service: RepositoryInventoryService | None = None,
        profile_store: ModelProfileStore | None = None,
        split_store: TaskSplitStore | None = None,
        registry: ToolRegistry | None = None,
        policy: ToolPolicy | None = None,
    ) -> None:
        self.plan_store = plan_store
        self.analysis_store = analysis_store
        self.decision_store = decision_store
        self.inventory_service = inventory_service or RepositoryInventoryService()
        self.profile_store = profile_store
        self.split_store = split_store
        self.registry = registry or builtin_registry()
        self.policy = policy or ToolPolicy()

    def check(
        self,
        plan_id: str,
        task_id: str,
        repository_root: Path,
        *,
        tool_id: str | None = None,
        revision: int | None = None,
        persist: bool = True,
    ) -> tuple[ToolPolicyDecision, ...]:
        plan = (
            self.plan_store.load_current(plan_id)
            if revision is None
            else self.plan_store.load_revision(plan_id, revision)
        )
        task = self._task(plan, task_id)
        definitions = (self.registry.get(tool_id),) if tool_id else self.registry.list()
        analysis = self._analysis(plan, task)
        stale, stale_reasons = self._analysis_staleness(plan, task, analysis, repository_root)
        approved = self._plan_approved(plan)
        decisions = tuple(
            self.policy.evaluate(
                plan,
                task,
                definition,
                registry_fingerprint=self.registry.fingerprint,
                analysis=analysis,
                analysis_stale=stale,
                analysis_stale_reasons=stale_reasons,
                plan_approved=approved,
            )
            for definition in definitions
        )
        if persist:
            for decision in decisions:
                self.decision_store.save(decision)
        return decisions

    def staleness(self, decision: ToolPolicyDecision, repository_root: Path) -> DecisionStaleness:
        reasons: list[str] = []
        try:
            current = self.plan_store.load_current(decision.plan_id)
            if current.current_revision != decision.plan_revision:
                reasons.append("plan revision changed")
            definition = self.registry.get(decision.tool_id)
            if definition.definition_fingerprint != decision.tool_fingerprint:
                reasons.append("tool definition changed")
            if self.registry.fingerprint != decision.registry_fingerprint:
                reasons.append("registry fingerprint changed")
            if (
                self.policy.version != decision.policy_version
                or self.policy.risk_derivation_version != decision.risk_derivation_version
            ):
                reasons.append("policy or risk derivation version changed")
            if not reasons:
                refreshed = self.check(
                    decision.plan_id,
                    decision.task_id,
                    repository_root,
                    tool_id=decision.tool_id,
                    revision=decision.plan_revision,
                    persist=False,
                )[0]
                if refreshed.semantic_fingerprint != decision.semantic_fingerprint:
                    reasons.append("semantic policy inputs changed")
        except Exception as exc:
            reasons.append(f"current linkage is unavailable: {exc}")
        return DecisionStaleness(stale=bool(reasons), reasons=tuple(sorted(set(reasons))))

    @staticmethod
    def _task(plan: PlanRevision, task_id: str) -> Task:
        task = next((item for item in plan.tasks if item.task_id == task_id), None)
        if task is None:
            raise ToolTaskMissingError(f"Task {task_id} does not belong to plan {plan.plan_id}")
        return task

    def _analysis(self, plan: PlanRevision, task: Task) -> TaskContextAnalysis | None:
        try:
            return self.analysis_store.load_current(plan.plan_id, plan.current_revision, task.task_id)
        except TaskContextNotFoundError:
            return None

    def _analysis_staleness(
        self,
        plan: PlanRevision,
        task: Task,
        analysis: TaskContextAnalysis | None,
        repository_root: Path,
    ) -> tuple[bool, tuple[str, ...]]:
        if analysis is None:
            return False, ()
        reasons: list[str] = []
        if analysis.graph_fingerprint != plan.graph_fingerprint or analysis.task_fingerprint != task.task_fingerprint:
            reasons.append("plan graph or task fingerprint changed")
        current = self.inventory_service.build(repository_root)
        if current.snapshot.semantic_fingerprint != analysis.repository_snapshot_fingerprint:
            reasons.append("repository snapshot changed")
        if self.profile_store is not None:
            try:
                profile = self.profile_store.find_by_id(analysis.profile_id)
                if (
                    profile.model_identity.model_digest != analysis.model_digest
                    or profile.operational_context_tokens != analysis.operational_context_tokens
                    or profile.maximum_recommended_input_tokens != analysis.maximum_recommended_input_tokens
                ):
                    reasons.append("model profile or exact digest changed")
            except ModelProfileError as exc:
                reasons.append(f"model profile unavailable: {exc}")
        return bool(reasons), tuple(sorted(reasons))

    def _plan_approved(self, plan: PlanRevision) -> bool:
        if self.split_store is None:
            return False
        return any(
            item.decision == ApprovalDecision.APPROVED
            and item.application_status == ApplicationStatus.APPLIED
            and item.applied_revision_number == plan.current_revision
            for item in self.split_store.list_approvals(plan.plan_id)
        )
