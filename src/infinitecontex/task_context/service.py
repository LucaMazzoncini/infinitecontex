"""Plan-linked repository resolution and exact-profile task context fit."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from infinitecontex.context_admission.policy import AdmissionPolicy
from infinitecontex.context_budget.calculator import ContextBudgetCalculator
from infinitecontex.context_budget.estimation import ConservativeTextEstimator, estimator_for_strategy
from infinitecontex.context_packing.models import ContextManifest, ManifestDecision, RankingPolicy
from infinitecontex.context_packing.service import ContextPackingService
from infinitecontex.context_packing.store import ContextManifestStore
from infinitecontex.model_profiles.errors import ModelProfileError
from infinitecontex.model_profiles.models import (
    CalibrationStatus,
    IdentityStrength,
    ModelProfile,
    normalize_model_name,
)
from infinitecontex.model_profiles.store import ModelProfileStore
from infinitecontex.planning.models import PlanRevision, Task
from infinitecontex.planning.store import PlanStore
from infinitecontex.task_context.candidates import (
    TaskContextCandidateBuilder,
    task_path_references,
    task_symbol_references,
)
from infinitecontex.task_context.errors import TaskContextProfileError
from infinitecontex.task_context.fingerprints import compute_analysis_fingerprint
from infinitecontex.task_context.models import (
    AnalysisCandidateRecord,
    AnalysisStaleness,
    PathReference,
    PathResolutionOutcome,
    ReferenceRequirement,
    RemediationAction,
    RepositoryInventory,
    SymbolReference,
    SymbolResolutionOutcome,
    TaskContextAnalysis,
    TaskContextDecision,
    TaskResolutionReport,
)
from infinitecontex.task_context.paths import FrozenPathResolution, PathResolver
from infinitecontex.task_context.policy import RepositoryResolutionPolicy
from infinitecontex.task_context.repository import RepositoryInventoryService
from infinitecontex.task_context.scopes import validate_task_scopes
from infinitecontex.task_context.store import TaskContextAnalysisStore
from infinitecontex.task_context.symbols import (
    FrozenSymbolResolution,
    PythonAstSymbolResolver,
    SymbolResolutionService,
)

_PATH_SUCCESS = {
    PathResolutionOutcome.RESOLVED,
    PathResolutionOutcome.RESOLVED_MULTIPLE,
    PathResolutionOutcome.FORBIDDEN,
}
_SYMBOL_SUCCESS = {
    SymbolResolutionOutcome.RESOLVED_EXACT,
    SymbolResolutionOutcome.RESOLVED_WITH_FILE_HINT,
}


class TaskContextService:
    def __init__(
        self,
        plan_store: PlanStore,
        profile_store: ModelProfileStore,
        calculator: ContextBudgetCalculator,
        inventory_service: RepositoryInventoryService,
        analysis_store: TaskContextAnalysisStore,
        manifest_store: ContextManifestStore | None = None,
        policy: RepositoryResolutionPolicy | None = None,
        ranking_policy: RankingPolicy | None = None,
        admission_policy: AdmissionPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.plan_store = plan_store
        self.profile_store = profile_store
        self.calculator = calculator
        self.inventory_service = inventory_service
        self.analysis_store = analysis_store
        self.manifest_store = manifest_store
        self.policy = policy or RepositoryResolutionPolicy()
        self.ranking_policy = ranking_policy or RankingPolicy()
        self.admission_policy = admission_policy or AdmissionPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))

    def resolve_plan(
        self,
        plan_id: str,
        repository_root: Path,
        *,
        revision: int | None = None,
        task_id: str | None = None,
        additional_path_references: dict[str, Sequence[PathReference]] | None = None,
        additional_symbol_references: dict[str, Sequence[SymbolReference]] | None = None,
    ) -> tuple[PlanRevision, RepositoryInventory, tuple[TaskResolutionReport, ...]]:
        plan = (
            self.plan_store.load_current(plan_id)
            if revision is None
            else self.plan_store.load_revision(plan_id, revision)
        )
        inventory = self.inventory_service.build(repository_root)
        selected = self._select_tasks(plan, task_id)
        reports = tuple(
            self._resolve_task(
                plan,
                task,
                repository_root,
                inventory,
                tuple((additional_path_references or {}).get(task.task_id, ())),
                tuple((additional_symbol_references or {}).get(task.task_id, ())),
            )[0]
            for task in selected
        )
        return plan, inventory, reports

    def context_fit(
        self,
        plan_id: str,
        repository_root: Path,
        *,
        model_name: str | None = None,
        digest: str | None = None,
        revision: int | None = None,
        task_id: str | None = None,
        persist: bool = True,
        additional_path_references: dict[str, Sequence[PathReference]] | None = None,
        additional_symbol_references: dict[str, Sequence[SymbolReference]] | None = None,
    ) -> tuple[TaskContextAnalysis, ...]:
        plan = (
            self.plan_store.load_current(plan_id)
            if revision is None
            else self.plan_store.load_revision(plan_id, revision)
        )
        return self.context_fit_revision(
            plan,
            repository_root,
            model_name=model_name,
            digest=digest,
            task_ids=(task_id,) if task_id else None,
            persist=persist,
            additional_path_references=additional_path_references,
            additional_symbol_references=additional_symbol_references,
        )

    def context_fit_revision(
        self,
        plan: PlanRevision,
        repository_root: Path,
        *,
        model_name: str | None = None,
        digest: str | None = None,
        task_ids: tuple[str, ...] | None = None,
        persist: bool = False,
        additional_path_references: dict[str, Sequence[PathReference]] | None = None,
        additional_symbol_references: dict[str, Sequence[SymbolReference]] | None = None,
    ) -> tuple[TaskContextAnalysis, ...]:
        """Fit selected tasks from an already validated, possibly transient revision."""
        profile = self._resolve_profile(plan, model_name, digest)
        inventory = self.inventory_service.build(repository_root)
        selected = (
            tuple(sorted(plan.tasks, key=lambda item: item.task_id))
            if task_ids is None
            else tuple(
                task for task in sorted(plan.tasks, key=lambda item: item.task_id) if task.task_id in set(task_ids)
            )
        )
        if task_ids is not None and len(selected) != len(set(task_ids)):
            raise ValueError("One or more selected tasks do not belong to the validated plan revision")
        packing = ContextPackingService(
            self.profile_store,
            self.calculator,
            manifest_store=self.manifest_store,
            policy=self.ranking_policy,
            clock=self.clock,
        )
        analyses: list[TaskContextAnalysis] = []
        for task in selected:
            report, paths, symbols = self._resolve_task(
                plan,
                task,
                repository_root,
                inventory,
                tuple((additional_path_references or {}).get(task.task_id, ())),
                tuple((additional_symbol_references or {}).get(task.task_id, ())),
            )
            candidates = TaskContextCandidateBuilder().build(task, inventory, paths, symbols)
            manifest = packing.pack(
                profile.model_identity.model_name,
                candidates,
                digest=profile.model_identity.model_digest,
                persist=persist and self.manifest_store is not None,
            )
            analysis = self._analysis(plan, task, profile, report, manifest)
            if persist:
                self.analysis_store.save(analysis)
            analyses.append(analysis)
        return tuple(analyses)

    def staleness(
        self,
        analysis: TaskContextAnalysis,
        repository_root: Path,
    ) -> AnalysisStaleness:
        reasons: list[str] = []
        try:
            plan = self.plan_store.load_revision(analysis.plan_id, analysis.plan_revision)
            task = next(item for item in plan.tasks if item.task_id == analysis.task_id)
            if plan.graph_fingerprint != analysis.graph_fingerprint:
                reasons.append("plan graph fingerprint changed")
            if task.task_fingerprint != analysis.task_fingerprint:
                reasons.append("task fingerprint changed")
        except (Exception,) as exc:
            reasons.append(f"linked plan revision is unavailable: {exc}")
        current = self.inventory_service.build(repository_root)
        if current.snapshot.semantic_fingerprint != analysis.repository_snapshot_fingerprint:
            reasons.append("repository snapshot or frozen source content changed")
        try:
            profile = self.profile_store.find_by_id(analysis.profile_id)
            if (
                profile.model_identity.model_digest != analysis.model_digest
                or profile.operational_context_tokens != analysis.operational_context_tokens
                or profile.maximum_recommended_input_tokens != analysis.maximum_recommended_input_tokens
                or profile.token_estimation_strategy != analysis.estimator_strategy
            ):
                reasons.append("model profile identity or operational policy changed")
        except ModelProfileError as exc:
            reasons.append(f"linked model profile is unavailable: {exc}")
        if (
            analysis.resolution_policy_id != self.policy.policy_id
            or analysis.resolution_policy_version != self.policy.version
        ):
            reasons.append("repository resolution policy changed")
        if (
            analysis.ranking_policy_id != self.ranking_policy.policy_id
            or analysis.ranking_policy_version != self.ranking_policy.version
            or analysis.packing_strategy_id != self.ranking_policy.packing_strategy
            or analysis.packing_strategy_version != self.ranking_policy.packing_version
        ):
            reasons.append("ranking or packing policy changed")
        return AnalysisStaleness(stale=bool(reasons), reasons=tuple(sorted(reasons)))

    def _resolve_task(
        self,
        plan: PlanRevision,
        task: Task,
        repository_root: Path,
        inventory: RepositoryInventory,
        additional_paths: tuple[PathReference, ...],
        additional_symbols: tuple[SymbolReference, ...],
    ) -> tuple[TaskResolutionReport, tuple[FrozenPathResolution, ...], tuple[FrozenSymbolResolution, ...]]:
        path_references = tuple(sorted((*task_path_references(task), *additional_paths), key=_path_reference_key))
        symbol_references = tuple(
            sorted((*task_symbol_references(task), *additional_symbols), key=_symbol_reference_key)
        )
        if len(path_references) > self.policy.maximum_path_references:
            raise ValueError(f"Task {task.task_id} exceeds the path-reference limit")
        if len(symbol_references) > self.policy.maximum_symbol_references:
            raise ValueError(f"Task {task.task_id} exceeds the symbol-reference limit")
        path_resolver = PathResolver(repository_root, inventory, self.policy)
        paths = tuple(path_resolver.resolve(reference) for reference in path_references)
        symbol_service = SymbolResolutionService(
            (PythonAstSymbolResolver(repository_root, inventory, path_resolver, self.policy),)
        )
        symbols = tuple(symbol_service.resolve(reference) for reference in symbol_references)
        conflicts = validate_task_scopes(task, inventory, tuple(item.resolution for item in paths), self.policy)
        warnings = [
            *(
                f"Optional path {item.resolution.reference.original_value} did not resolve: "
                f"{item.resolution.outcome.value}"
                for item in paths
                if item.resolution.reference.requirement == ReferenceRequirement.OPTIONAL
                and item.resolution.outcome not in _PATH_SUCCESS
            ),
            *(
                f"Optional symbol {item.resolution.reference.original_reference} did not resolve: "
                f"{item.resolution.outcome.value}"
                for item in symbols
                if item.resolution.reference.requirement == ReferenceRequirement.OPTIONAL
                and item.resolution.outcome not in _SYMBOL_SUCCESS
            ),
        ]
        report = TaskResolutionReport(
            plan_id=plan.plan_id,
            plan_revision=plan.current_revision,
            task_id=task.task_id,
            task_fingerprint=task.task_fingerprint,
            repository_snapshot=inventory.snapshot,
            path_resolutions=tuple(item.resolution for item in paths),
            symbol_resolutions=tuple(item.resolution for item in symbols),
            scope_conflicts=conflicts,
            warnings=tuple(sorted(warnings)),
        )
        return report, paths, symbols

    def _resolve_profile(self, plan: PlanRevision, model_name: str | None, digest: str | None) -> ModelProfile:
        try:
            if plan.model_profile_ref:
                profile = self.profile_store.find_by_id(plan.model_profile_ref)
                if model_name and profile.model_identity.normalized_model_name != normalize_model_name(model_name):
                    raise TaskContextProfileError("CLI model does not match the plan's exact model-profile reference")
                if digest and profile.model_identity.model_digest != digest:
                    raise TaskContextProfileError("CLI digest does not match the plan's exact model-profile reference")
            else:
                if not model_name:
                    raise TaskContextProfileError("Plan has no model profile reference; supply --model")
                from infinitecontex.context_budget.service import ContextBudgetService

                resolver = ContextBudgetService(
                    self.profile_store,
                    ConservativeTextEstimator(),
                    self.calculator,
                    clock=self.clock,
                )
                profile = resolver.resolve_profile(model_name, digest)
        except ModelProfileError as exc:
            raise TaskContextProfileError(str(exc)) from exc
        identity = profile.model_identity
        if identity.identity_strength != IdentityStrength.VERIFIED or not identity.model_digest:
            raise TaskContextProfileError("Task context fit requires a verified exact model digest")
        if profile.calibration_status == CalibrationStatus.STALE and not self.admission_policy.permit_stale_profiles:
            raise TaskContextProfileError("Selected model profile is stale; recreate it before task fit")
        if profile.calibration_status == CalibrationStatus.UNCALIBRATED:
            if (
                not self.admission_policy.permit_conservative_uncalibrated
                or profile.operational_context_tokens > self.admission_policy.maximum_uncalibrated_operational_tokens
            ):
                raise TaskContextProfileError("Uncalibrated profile is outside the conservative admission policy")
        return profile

    def _analysis(
        self,
        plan: PlanRevision,
        task: Task,
        profile: ModelProfile,
        report: TaskResolutionReport,
        manifest: ContextManifest,
    ) -> TaskContextAnalysis:
        required_path_failures = [
            item
            for item in report.path_resolutions
            if item.reference.requirement == ReferenceRequirement.REQUIRED and item.outcome not in _PATH_SUCCESS
        ]
        required_symbol_failures = [
            item
            for item in report.symbol_resolutions
            if item.reference.requirement == ReferenceRequirement.REQUIRED and item.outcome not in _SYMBOL_SUCCESS
        ]
        blocking_conflicts = [item for item in report.scope_conflicts if item.blocking]
        mandatory_excluded = manifest.decision == ManifestDecision.MANDATORY_OVERFLOW
        declared_maximum = task.context_requirements.maximum_context_tokens
        declared_deficit = (
            max(0, manifest.included_token_total - declared_maximum) if declared_maximum is not None else 0
        )
        utilization = (
            manifest.included_token_total * 10000 // manifest.maximum_recommended_input_tokens
            if manifest.maximum_recommended_input_tokens
            else 10000
        )
        if any(item.outcome == SymbolResolutionOutcome.UNSUPPORTED_LANGUAGE for item in required_symbol_failures):
            decision = TaskContextDecision.UNSUPPORTED_REQUIRED_SYMBOL
        elif required_path_failures or required_symbol_failures:
            decision = TaskContextDecision.REQUIRED_REFERENCE_UNRESOLVED
        elif blocking_conflicts:
            decision = TaskContextDecision.SCOPE_CONFLICT
        elif mandatory_excluded or manifest.decision == ManifestDecision.INVALID_REQUEST or declared_deficit:
            decision = TaskContextDecision.SPLIT_REQUIRED
        elif utilization >= self.admission_policy.warning_threshold_basis_points:
            decision = TaskContextDecision.FITS_HARD_LIMIT
        elif manifest.decision == ManifestDecision.OPTIONAL_EXCLUDED:
            decision = TaskContextDecision.FITS_WITH_WARNING
        else:
            decision = TaskContextDecision.FITS_TARGET
        passing = decision in {
            TaskContextDecision.FITS_TARGET,
            TaskContextDecision.FITS_WITH_WARNING,
            TaskContextDecision.FITS_HARD_LIMIT,
        }
        errors = [
            *(
                f"Required path {item.reference.original_value}: {item.outcome.value}"
                for item in required_path_failures
            ),
            *(
                f"Required symbol {item.reference.original_reference}: {item.outcome.value}"
                for item in required_symbol_failures
            ),
            *(item.message for item in blocking_conflicts),
        ]
        included_records = tuple(
            AnalysisCandidateRecord(
                candidate_id=item.candidate.candidate_id,
                candidate_fingerprint=item.candidate.candidate_fingerprint,
                category=item.candidate.category,
                token_count=item.candidate.token_count,
                provenance=item.candidate.token_provenance,
                source_path=item.candidate.source_path,
                source_range=item.candidate.source_range,
                symbol_id=item.candidate.symbol_id,
                content_hash=item.candidate.content_hash,
                reason=item.reason,
            )
            for item in manifest.included
        )
        excluded_records = tuple(
            AnalysisCandidateRecord(
                candidate_id=item.candidate_id,
                candidate_fingerprint=item.candidate_fingerprint,
                category=item.category,
                token_count=item.token_count,
                reason=f"{item.reason}: {item.detail}",
            )
            for item in manifest.excluded
        )
        estimator = estimator_for_strategy(profile.token_estimation_strategy)
        provisional = TaskContextAnalysis(
            analysis_id="task-context-" + "0" * 24,
            semantic_fingerprint="0" * 64,
            plan_id=plan.plan_id,
            plan_revision=plan.current_revision,
            graph_fingerprint=plan.graph_fingerprint,
            task_id=task.task_id,
            task_fingerprint=task.task_fingerprint,
            repository_snapshot_id=report.repository_snapshot.snapshot_id,
            repository_snapshot_fingerprint=report.repository_snapshot.semantic_fingerprint,
            profile_id=profile.profile_id,
            provider=profile.model_identity.provider,
            normalized_model_name=profile.model_identity.normalized_model_name,
            model_digest=profile.model_identity.model_digest or "",
            operational_context_tokens=profile.operational_context_tokens,
            maximum_recommended_input_tokens=profile.maximum_recommended_input_tokens,
            estimator_strategy=profile.token_estimation_strategy,
            estimator_version=estimator.estimate("").strategy_version,
            resolution_policy_id=self.policy.policy_id,
            resolution_policy_version=self.policy.version,
            ranking_policy_id=manifest.ranking_policy_id,
            ranking_policy_version=manifest.ranking_policy_version,
            packing_strategy_id=manifest.packing_strategy_id,
            packing_strategy_version=manifest.packing_strategy_version,
            context_manifest_id=manifest.manifest_id,
            context_manifest_fingerprint=manifest.manifest_fingerprint,
            decision=decision,
            passing=passing,
            mandatory_token_total=manifest.mandatory_token_total,
            optional_token_total=manifest.optional_token_total,
            packed_token_total=manifest.included_token_total,
            remaining_input_tokens=manifest.remaining_pack_tokens,
            token_deficit=max(manifest.token_deficit, declared_deficit),
            resolved_reference_count=sum(item.outcome in _PATH_SUCCESS for item in report.path_resolutions)
            + sum(item.outcome in _SYMBOL_SUCCESS for item in report.symbol_resolutions),
            unresolved_reference_count=len(required_path_failures)
            + len(required_symbol_failures)
            + sum(
                item.reference.requirement == ReferenceRequirement.OPTIONAL and item.outcome not in _PATH_SUCCESS
                for item in report.path_resolutions
            )
            + sum(
                item.reference.requirement == ReferenceRequirement.OPTIONAL and item.outcome not in _SYMBOL_SUCCESS
                for item in report.symbol_resolutions
            ),
            scope_conflict_count=len(report.scope_conflicts),
            path_resolutions=report.path_resolutions,
            symbol_resolutions=report.symbol_resolutions,
            scope_conflicts=report.scope_conflicts,
            included_candidates=included_records,
            excluded_candidates=excluded_records,
            warnings=tuple(sorted((*report.warnings, *manifest.warnings))),
            errors=tuple(sorted(errors)),
            remediation_actions=_remediation(
                decision,
                required_path_failures,
                required_symbol_failures,
                max(manifest.token_deficit, declared_deficit),
            ),
            created_at=self.clock(),
        )
        fingerprint = compute_analysis_fingerprint(provisional)
        return provisional.model_copy(
            update={"analysis_id": f"task-context-{fingerprint[:24]}", "semantic_fingerprint": fingerprint}
        )

    @staticmethod
    def _select_tasks(plan: PlanRevision, task_id: str | None) -> tuple[Task, ...]:
        if task_id is None:
            return tuple(sorted(plan.tasks, key=lambda item: item.task_id))
        selected = tuple(item for item in plan.tasks if item.task_id == task_id)
        if not selected:
            raise ValueError(f"Task {task_id} does not belong to plan {plan.plan_id}")
        return selected


def _remediation(
    decision: TaskContextDecision,
    path_failures: Sequence[object],
    symbol_failures: Sequence[object],
    deficit: int,
) -> tuple[RemediationAction, ...]:
    actions: list[RemediationAction] = []
    if path_failures:
        actions.append(
            RemediationAction(
                code="resolve_required_path",
                message="Correct or remove the missing required path.",
            )
        )
    if symbol_failures:
        actions.append(
            RemediationAction(
                code="resolve_required_symbol",
                message="Add an exact Python qualified name/file hint or replace unsupported symbols with file ranges.",
            )
        )
    if decision == TaskContextDecision.SCOPE_CONFLICT:
        actions.append(
            RemediationAction(code="narrow_scope", message="Separate affected and forbidden scopes explicitly.")
        )
    if decision == TaskContextDecision.SPLIT_REQUIRED:
        actions.extend(
            (
                RemediationAction(
                    code="narrow_whole_file",
                    message="Narrow whole-file references to explicit symbols or source ranges.",
                ),
                RemediationAction(
                    code="split_task",
                    message=f"Split implementation and validation declarations; current token deficit is {deficit}.",
                ),
            )
        )
    return tuple(actions)


def _path_reference_key(reference: PathReference) -> tuple[str, str, str, int, int]:
    return (
        reference.source_field,
        reference.original_value.replace("\\", "/"),
        reference.requirement,
        reference.line_start or 0,
        reference.line_end or 0,
    )


def _symbol_reference_key(reference: SymbolReference) -> tuple[str, str, str, str]:
    return (
        reference.language.casefold(),
        (reference.file_hint or "").replace("\\", "/"),
        reference.qualified_name or reference.symbol_name,
        reference.requirement,
    )
