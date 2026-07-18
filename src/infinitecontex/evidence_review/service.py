"""Deterministic G5 evidence verification, aggregation, and human decision service."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from infinitecontex.events.logger import EventLogger
from infinitecontex.evidence_review.errors import (
    ReviewCriterionError,
    ReviewDecisionError,
    ReviewEvidenceError,
    ReviewPlanError,
    ReviewTaskError,
)
from infinitecontex.evidence_review.models import (
    ActorType,
    AggregationRule,
    Confidence,
    EvidenceContribution,
    EvidenceReview,
    EvidenceReviewDecision,
    FreshnessResult,
    IntegrityResult,
    ReviewDecisionValue,
    ReviewOutcome,
)
from infinitecontex.evidence_review.policy import (
    MAX_EVIDENCE_PER_REVIEW,
    SUPPORTED_METHODS,
    EvidenceReviewPolicy,
    model_fingerprint,
)
from infinitecontex.evidence_review.store import EvidenceReviewStore
from infinitecontex.planning.models import AcceptanceCriterion, EvidenceType, PlanRevision, Task
from infinitecontex.planning.store import PlanStore
from infinitecontex.task_context.repository import RepositoryInventoryService
from infinitecontex.task_context.store import TaskContextAnalysisStore
from infinitecontex.tools.execution_service import NoCommandGitStateProvider
from infinitecontex.tools.sensitive import SensitivePathPolicy
from infinitecontex.tools.validation_definitions import ValidationCommandRegistry
from infinitecontex.tools.validation_models import ExitClassification, ValidationEvidence
from infinitecontex.tools.validation_store import ValidationStore


class EvidenceReviewService:
    def __init__(
        self,
        plan_store: PlanStore,
        validation_store: ValidationStore,
        review_store: EvidenceReviewStore,
        *,
        command_registry: ValidationCommandRegistry | None = None,
        analysis_store: TaskContextAnalysisStore | None = None,
        inventory_service: RepositoryInventoryService | None = None,
        policy: EvidenceReviewPolicy | None = None,
        event_logger: EventLogger | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.plan_store, self.validation_store, self.store = plan_store, validation_store, review_store
        self.commands = command_registry or ValidationCommandRegistry()
        self.analysis_store = analysis_store
        self.inventory = inventory_service or RepositoryInventoryService(
            git_provider=NoCommandGitStateProvider(), additional_path_filter=SensitivePathPolicy().permits
        )
        self.policy = policy or EvidenceReviewPolicy()
        self.event_logger, self.clock = event_logger, clock or (lambda: datetime.now(UTC))

    def review(
        self,
        root: Path,
        plan_id: str,
        task_id: str,
        criterion_id: str,
        *,
        evidence_ids: Iterable[str] | None = None,
        revision: int | None = None,
    ) -> EvidenceReview:
        self._event("evidence_review_started", {"plan_id": plan_id, "task_id": task_id, "criterion_id": criterion_id})
        plan = (
            self.plan_store.load_current(plan_id)
            if revision is None
            else self.plan_store.load_revision(plan_id, revision)
        )
        if revision is not None and plan.current_revision != revision:
            raise ReviewPlanError("Requested evidence review revision is not current")
        task = next((item for item in plan.tasks if item.task_id == task_id), None)
        if task is None:
            raise ReviewTaskError("Task was not found in the exact plan revision")
        criterion = next((item for item in task.acceptance_criteria if item.criterion_id == criterion_id), None)
        if criterion is None:
            raise ReviewCriterionError("Acceptance criterion was not found")
        criterion_fp = model_fingerprint(criterion)
        method = criterion.verification_method.strip().casefold().replace("-", "_").replace(" ", "_")
        contract = SUPPORTED_METHODS.get(method)
        if contract is None or contract[0] != criterion.required_evidence_type:
            return self._empty_review(
                plan,
                task,
                criterion,
                criterion_fp,
                ReviewOutcome.CRITERION_UNSUPPORTED,
                ("unsupported_criterion_contract",),
                ("Use a supported exact verification method and evidence type.",),
            )
        selected = self._select_evidence(plan_id, task_id, criterion_id, evidence_ids)
        if len(selected) > MAX_EVIDENCE_PER_REVIEW:
            raise ReviewEvidenceError(f"Evidence review exceeds the {MAX_EVIDENCE_PER_REVIEW}-record limit")
        current_snapshot = self.inventory.build(root.resolve(strict=True)).snapshot.semantic_fingerprint
        contributions: list[EvidenceContribution] = []
        seen_execution: set[str] = set()
        seen_semantic: set[tuple[str, str, str, str, str, str]] = set()
        accepted: list[str] = []
        rejected: list[str] = []
        duplicates: list[str] = []
        conflicts: list[str] = []
        warnings: list[str] = []
        reasons: list[str] = []
        snapshots: set[str] = set()
        command_ids: set[str] = set()
        command_fps: set[str] = set()
        execution_ids: set[str] = set()
        execution_fps: set[str] = set()
        provenances: set[str] = set()
        classifications: list[ExitClassification] = []
        invalid = stale = contaminated = incomplete = irrelevant = False
        for evidence in sorted(selected, key=lambda item: item.evidence_id):
            contribution, state = self._verify_one(
                evidence, plan, task, criterion, contract[1], current_snapshot, seen_execution, seen_semantic
            )
            contributions.append(contribution)
            snapshots.add(contribution.repository_snapshot)
            command_ids.add(contribution.command_id)
            command_fps.add(contribution.command_fingerprint)
            execution_ids.add(contribution.execution_id)
            execution_fps.add(contribution.execution_fingerprint)
            provenances.add("g4_validation_command")
            classifications.append(ExitClassification(contribution.classification))
            if contribution.redacted:
                warnings.append("redacted_output")
            if contribution.disposition == "accepted":
                accepted.append(evidence.evidence_id)
            elif contribution.disposition == "duplicate":
                duplicates.append(evidence.evidence_id)
            elif contribution.disposition == "conflicting":
                conflicts.append(evidence.evidence_id)
            else:
                rejected.append(evidence.evidence_id)
            invalid |= state == "invalid"
            stale |= state == "stale"
            contaminated |= state == "contaminated"
            incomplete |= state == "incomplete"
            irrelevant |= state == "irrelevant"
        unique_classifications = set(classifications)
        pass_ids = [
            c.evidence_id
            for c in contributions
            if c.classification == ExitClassification.PASSED.value and c.disposition != "duplicate"
        ]
        fail_ids = [
            c.evidence_id
            for c in contributions
            if c.classification == ExitClassification.VALIDATION_FAILED.value and c.disposition != "duplicate"
        ]
        if pass_ids and fail_ids and len(snapshots) == 1:
            conflict_set = set(pass_ids + fail_ids)
            conflicts[:] = sorted(conflict_set)
            contributions = [
                item.model_copy(
                    update={
                        "disposition": "conflicting",
                        "reason_codes": tuple(sorted(set(item.reason_codes + ("conflicting_results",)))),
                    }
                )
                if item.evidence_id in conflict_set
                else item
                for item in contributions
            ]
            accepted[:] = [item for item in accepted if item not in conflict_set]
            rejected[:] = [item for item in rejected if item not in conflict_set]
        minimum = max(
            1,
            sum(
                1
                for requirement in task.required_evidence
                if requirement.mandatory and requirement.evidence_type == criterion.required_evidence_type
            ),
        )
        missing = () if len(accepted) >= minimum else (f"minimum_evidence_count:{minimum}",)
        outcome, supports, confidence = self._outcome(
            selected,
            invalid,
            stale,
            contaminated,
            incomplete,
            irrelevant,
            conflicts,
            accepted,
            unique_classifications,
            minimum,
            bool(warnings),
        )
        reasons.extend(_reason_codes(outcome))
        analysis_fp = None
        if self.analysis_store is not None:
            try:
                analysis = self.analysis_store.load_current(plan_id, plan.current_revision, task_id)
                if analysis.task_fingerprint == task.task_fingerprint:
                    analysis_fp = analysis.semantic_fingerprint
            except ValueError:
                warnings.append("task_context_analysis_unavailable")
        provisional = EvidenceReview(
            review_id="evidence-review-" + "0" * 24,
            semantic_fingerprint="0" * 64,
            review_policy_fingerprint=self.policy.fingerprint,
            plan_id=plan_id,
            plan_revision=plan.current_revision,
            graph_fingerprint=plan.graph_fingerprint,
            task_id=task.task_id,
            task_fingerprint=task.task_fingerprint,
            task_context_analysis_fingerprint=analysis_fp,
            criterion_id=criterion.criterion_id,
            criterion_fingerprint=criterion_fp,
            criterion_status_before=criterion.status.value,
            required_evidence_type=criterion.required_evidence_type.value,
            verification_method=criterion.verification_method,
            mandatory=criterion.mandatory,
            evidence_ids=tuple(item.evidence_id for item in sorted(selected, key=lambda item: item.evidence_id)),
            evidence_fingerprints=tuple(
                item.fingerprint for item in sorted(selected, key=lambda item: item.evidence_id)
            ),
            repository_snapshot_fingerprints=tuple(sorted(snapshots)),
            command_definition_ids=tuple(sorted(command_ids)),
            command_definition_fingerprints=tuple(sorted(command_fps)),
            validation_execution_ids=tuple(sorted(execution_ids)),
            validation_execution_fingerprints=tuple(sorted(execution_fps)),
            evidence_provenance=tuple(sorted(provenances)),
            outcome=outcome,
            evidence_supports_criterion=supports,
            evidence_count=len(selected),
            accepted_evidence_ids=tuple(sorted(accepted)),
            rejected_evidence_ids=tuple(sorted(rejected)),
            duplicate_evidence_ids=tuple(sorted(duplicates)),
            conflicting_evidence_ids=tuple(sorted(conflicts)),
            missing_requirements=missing,
            freshness_result=FreshnessResult.STALE
            if stale
            else FreshnessResult.MIXED
            if len(snapshots) > 1
            else FreshnessResult.FRESH,
            repository_state_result=IntegrityResult.FAILED if stale or contaminated else IntegrityResult.VERIFIED,
            command_identity_result=IntegrityResult.FAILED if invalid or irrelevant else IntegrityResult.VERIFIED,
            output_integrity_result=IntegrityResult.FAILED
            if incomplete or invalid
            else IntegrityResult.WARNING
            if warnings
            else IntegrityResult.VERIFIED,
            contamination_result=IntegrityResult.FAILED if contaminated else IntegrityResult.VERIFIED,
            aggregation_rule=AggregationRule.ALL_REQUIRED,
            aggregation_result=f"{len(accepted)}/{minimum} unique supporting evidence",
            confidence=confidence,
            contributions=tuple(contributions),
            reason_codes=tuple(sorted(set(reasons))),
            warnings=tuple(sorted(set(warnings))),
            errors=tuple(
                sorted(set(c for item in contributions for c in item.reason_codes if c.startswith("invalid_")))
            ),
            remediation=_remediation(outcome),
            created_at=self.clock(),
        )
        fingerprint = model_fingerprint(provisional, "semantic_fingerprint", "review_id", "created_at")
        value = provisional.model_copy(
            update={"semantic_fingerprint": fingerprint, "review_id": f"evidence-review-{fingerprint[:24]}"}
        )
        self.store.save_review(value)
        self._event("evidence_review_persisted", {"review_id": value.review_id, "outcome": value.outcome.value})
        return value

    def decide(
        self,
        plan_id: str,
        review_id: str,
        actor: str,
        decision: ReviewDecisionValue,
        *,
        reason: str,
        actor_type: ActorType = ActorType.HUMAN,
        warnings_acknowledged: bool = False,
    ) -> EvidenceReviewDecision:
        review = self.store.load_review(plan_id, review_id)
        self.assert_fresh(review)
        provisional = EvidenceReviewDecision(
            decision_id="evidence-review-decision-" + "0" * 24,
            decision_fingerprint="0" * 64,
            review_id=review.review_id,
            review_fingerprint=review.semantic_fingerprint,
            plan_id=plan_id,
            plan_revision=review.plan_revision,
            task_id=review.task_id,
            task_fingerprint=review.task_fingerprint,
            criterion_id=review.criterion_id,
            criterion_fingerprint=review.criterion_fingerprint,
            actor=actor,
            actor_type=actor_type,
            decision=decision,
            reason=reason,
            warnings_acknowledged=warnings_acknowledged,
            decided_at=self.clock(),
        )
        fp = model_fingerprint(provisional, "decision_fingerprint", "decision_id", "decided_at")
        value = provisional.model_copy(
            update={"decision_fingerprint": fp, "decision_id": f"evidence-review-decision-{fp[:24]}"}
        )
        self.store.save_decision(value)
        self._event("evidence_review_decision_recorded", {"review_id": review_id, "decision": decision.value})
        return value

    def assert_fresh(self, review: EvidenceReview) -> None:
        if review.review_policy_fingerprint != self.policy.fingerprint:
            raise ReviewDecisionError("Evidence review policy changed; create a new review")
        plan = self.plan_store.load_current(review.plan_id)
        task = next((item for item in plan.tasks if item.task_id == review.task_id), None)
        criterion = (
            None
            if task is None
            else next((item for item in task.acceptance_criteria if item.criterion_id == review.criterion_id), None)
        )
        if (
            plan.current_revision != review.plan_revision
            or plan.graph_fingerprint != review.graph_fingerprint
            or task is None
            or task.task_fingerprint != review.task_fingerprint
            or criterion is None
            or model_fingerprint(criterion) != review.criterion_fingerprint
        ):
            raise ReviewDecisionError("Evidence review is stale; create a new review")
        actual = tuple(self.validation_store.load_evidence(item).fingerprint for item in review.evidence_ids)
        if actual != review.evidence_fingerprints:
            raise ReviewDecisionError("Evidence changed after review; create a new review")

    def _select_evidence(
        self, plan_id: str, task_id: str, criterion_id: str, evidence_ids: Iterable[str] | None
    ) -> tuple[ValidationEvidence, ...]:
        if evidence_ids is not None:
            ids = tuple(sorted(set(evidence_ids)))
            return tuple(self.validation_store.load_evidence(item) for item in ids)
        return tuple(
            item
            for item in self.validation_store.list_evidence(limit=MAX_EVIDENCE_PER_REVIEW)
            if item.plan_id == plan_id and item.task_id == task_id and item.criterion_id == criterion_id
        )

    def _verify_one(
        self,
        evidence: ValidationEvidence,
        plan: PlanRevision,
        task: Task,
        criterion: AcceptanceCriterion,
        categories: frozenset[object],
        current_snapshot: str,
        seen_execution: set[str],
        seen_semantic: set[tuple[str, str, str, str, str, str]],
    ) -> tuple[EvidenceContribution, str]:
        reasons: list[str] = []
        state = "accepted"
        record = self.validation_store.load_record(evidence.execution_id)
        proposal = self.validation_store.load_proposal(plan.plan_id, record.proposal_id)
        approvals = self.validation_store.list_approvals(plan.plan_id, record.proposal_id)
        approval = next((item for item in approvals if item.approval_id == record.approval_id), None)
        definition = self.commands.get(record.command_id)
        if (
            evidence.plan_id != plan.plan_id
            or record.plan_id != plan.plan_id
            or evidence.task_id != task.task_id
            or record.task_id != task.task_id
            or record.plan_revision != plan.current_revision
            or record.task_fingerprint != task.task_fingerprint
        ):
            reasons.append("invalid_plan_task_linkage")
            state = "invalid"
        if evidence.criterion_id != criterion.criterion_id:
            reasons.append("irrelevant_criterion_link")
            state = "irrelevant"
        if (
            evidence.classification != record.classification
            or evidence.repository_snapshot_before != record.repository_snapshot_before
            or evidence.repository_snapshot_after != record.repository_snapshot_after
            or evidence.stdout_sha256 != record.stdout.sha256
            or evidence.stderr_sha256 != record.stderr.sha256
        ):
            reasons.append("invalid_evidence_execution_linkage")
            state = "invalid"
        if (
            proposal.semantic_fingerprint != record.proposal_fingerprint
            or approval is None
            or approval.approval_fingerprint != record.approval_fingerprint
            or proposal.command_fingerprint != record.command_fingerprint
            or definition.fingerprint != record.command_fingerprint
        ):
            reasons.append("invalid_proposal_approval_command_linkage")
            state = "invalid"
        if definition.category not in categories:
            reasons.append("irrelevant_command_category")
            state = "irrelevant"
        derived_type = _evidence_type(definition.category)
        if derived_type != criterion.required_evidence_type:
            reasons.append("unsupported_evidence_type")
            state = "irrelevant"
        stale = (
            record.repository_snapshot_before != record.repository_snapshot_after
            or record.repository_snapshot_after != current_snapshot
            or proposal.repository_snapshot_fingerprint != record.repository_snapshot_before
        )
        if stale:
            reasons.append("stale_repository_snapshot")
            state = "stale"
        contaminated = (
            bool(record.unexpected_changed_paths)
            or record.classification == ExitClassification.REPOSITORY_MUTATION_DETECTED
        )
        if contaminated:
            reasons.append("repository_mutation_detected")
            state = "contaminated"
        incomplete = (
            record.stdout.truncated
            or record.stderr.truncated
            or record.classification == ExitClassification.OUTPUT_LIMIT_EXCEEDED
        )
        if incomplete:
            reasons.append("output_incomplete")
            state = "incomplete"
        semantic = (
            record.command_id,
            record.arguments_fingerprint,
            record.repository_snapshot_after,
            record.stdout.sha256,
            record.stderr.sha256,
            record.classification.value,
        )
        duplicate = record.execution_id in seen_execution or semantic in seen_semantic
        seen_execution.add(record.execution_id)
        seen_semantic.add(semantic)
        disposition: Literal["accepted", "rejected", "duplicate", "conflicting"] = "accepted"
        if duplicate:
            disposition = "duplicate"
            reasons.append("duplicate_evidence")
        elif state != "accepted" or record.classification != ExitClassification.PASSED:
            disposition = "rejected"
        return EvidenceContribution(
            evidence_id=evidence.evidence_id,
            evidence_fingerprint=evidence.fingerprint,
            execution_id=record.execution_id,
            execution_fingerprint=record.record_fingerprint,
            command_id=record.command_id,
            command_fingerprint=record.command_fingerprint,
            repository_snapshot=record.repository_snapshot_after,
            classification=record.classification.value,
            disposition=disposition,
            reason_codes=tuple(sorted(reasons)),
            output_complete=not incomplete,
            redacted=record.stdout.redacted or record.stderr.redacted,
            contaminated=contaminated,
        ), state

    @staticmethod
    def _outcome(
        selected: tuple[ValidationEvidence, ...],
        invalid: bool,
        stale: bool,
        contaminated: bool,
        incomplete: bool,
        irrelevant: bool,
        conflicts: list[str],
        accepted: list[str],
        classifications: set[ExitClassification],
        minimum: int,
        warnings: bool,
    ) -> tuple[ReviewOutcome, bool, Confidence]:
        if not selected:
            return ReviewOutcome.MISSING_EVIDENCE, False, Confidence.NONE
        if invalid:
            return ReviewOutcome.INVALID_EVIDENCE, False, Confidence.NONE
        if contaminated:
            return ReviewOutcome.CONTAMINATED_EVIDENCE, False, Confidence.NONE
        if stale:
            return ReviewOutcome.STALE_EVIDENCE, False, Confidence.NONE
        if conflicts:
            return ReviewOutcome.CONFLICTING_EVIDENCE, False, Confidence.NONE
        if incomplete:
            return ReviewOutcome.INCONCLUSIVE, False, Confidence.NONE
        if irrelevant and not accepted:
            return ReviewOutcome.IRRELEVANT_EVIDENCE, False, Confidence.NONE
        if ExitClassification.VALIDATION_FAILED in classifications:
            return ReviewOutcome.FAILED, False, Confidence.HIGH
        if classifications & {
            ExitClassification.PROCESS_ERROR,
            ExitClassification.TIMED_OUT,
            ExitClassification.CANCELLED,
            ExitClassification.INTERNAL_RUNNER_ERROR,
            ExitClassification.POLICY_REJECTED,
        }:
            return ReviewOutcome.INCONCLUSIVE, False, Confidence.NONE
        if len(accepted) < minimum:
            return ReviewOutcome.INSUFFICIENT_EVIDENCE, False, Confidence.NONE
        return (
            (ReviewOutcome.SUPPORTED_WITH_WARNING, True, Confidence.MEDIUM)
            if warnings
            else (ReviewOutcome.SUPPORTED, True, Confidence.HIGH)
        )

    def _empty_review(
        self,
        plan: PlanRevision,
        task: Task,
        criterion: AcceptanceCriterion,
        criterion_fp: str,
        outcome: ReviewOutcome,
        reasons: tuple[str, ...],
        remediation: tuple[str, ...],
    ) -> EvidenceReview:
        provisional = EvidenceReview(
            review_id="evidence-review-" + "0" * 24,
            semantic_fingerprint="0" * 64,
            review_policy_fingerprint=self.policy.fingerprint,
            plan_id=plan.plan_id,
            plan_revision=plan.current_revision,
            graph_fingerprint=plan.graph_fingerprint,
            task_id=task.task_id,
            task_fingerprint=task.task_fingerprint,
            criterion_id=criterion.criterion_id,
            criterion_fingerprint=criterion_fp,
            criterion_status_before=criterion.status.value,
            required_evidence_type=criterion.required_evidence_type.value,
            verification_method=criterion.verification_method,
            mandatory=criterion.mandatory,
            evidence_ids=(),
            evidence_fingerprints=(),
            repository_snapshot_fingerprints=(),
            command_definition_ids=(),
            command_definition_fingerprints=(),
            validation_execution_ids=(),
            validation_execution_fingerprints=(),
            evidence_provenance=(),
            outcome=outcome,
            evidence_supports_criterion=False,
            evidence_count=0,
            missing_requirements=("supported_criterion_contract",),
            freshness_result=FreshnessResult.NOT_EVALUATED,
            repository_state_result=IntegrityResult.NOT_EVALUATED,
            command_identity_result=IntegrityResult.FAILED,
            output_integrity_result=IntegrityResult.NOT_EVALUATED,
            contamination_result=IntegrityResult.NOT_EVALUATED,
            aggregation_rule=AggregationRule.ALL_REQUIRED,
            aggregation_result="not evaluated",
            confidence=Confidence.NONE,
            contributions=(),
            reason_codes=reasons,
            remediation=remediation,
            created_at=self.clock(),
        )
        fp = model_fingerprint(provisional, "semantic_fingerprint", "review_id", "created_at")
        value = provisional.model_copy(update={"semantic_fingerprint": fp, "review_id": f"evidence-review-{fp[:24]}"})
        self.store.save_review(value)
        return value

    def _event(self, name: str, payload: dict[str, object]) -> None:
        if self.event_logger:
            self.event_logger.log(name, payload)


def _evidence_type(category: object) -> EvidenceType:
    value = getattr(category, "value", category)
    if value == "test":
        return EvidenceType.TEST_RESULT
    if value == "build":
        return EvidenceType.BUILD_RESULT
    return EvidenceType.STATIC_ANALYSIS_RESULT


def _reason_codes(outcome: ReviewOutcome) -> tuple[str, ...]:
    return (f"review_{outcome.value}",)


def _remediation(outcome: ReviewOutcome) -> tuple[str, ...]:
    actions = {
        ReviewOutcome.MISSING_EVIDENCE: "Run and explicitly link an approved validation to this criterion.",
        ReviewOutcome.STALE_EVIDENCE: "Run validation again against the current repository and create a new review.",
        ReviewOutcome.CONTAMINATED_EVIDENCE: "Inspect repository changes, then rerun validation from a stable state.",
        ReviewOutcome.CONFLICTING_EVIDENCE: (
            "Resolve conflicting validation results and submit a consistent evidence set."
        ),
        ReviewOutcome.INSUFFICIENT_EVIDENCE: "Provide the remaining required unique evidence.",
        ReviewOutcome.INCONCLUSIVE: ("Rerun validation without timeout, infrastructure failure, or truncated output."),
        ReviewOutcome.IRRELEVANT_EVIDENCE: (
            "Select evidence explicitly linked to this criterion and verification method."
        ),
        ReviewOutcome.INVALID_EVIDENCE: "Discard the tampered or malformed record and create fresh evidence.",
        ReviewOutcome.FAILED: "Fix the validation failure and collect fresh passing evidence.",
    }
    return (actions[outcome],) if outcome in actions else ()
