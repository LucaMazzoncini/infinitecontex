"""Deterministic proposal, approval, and apply orchestration for G6."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from infinitecontex.events.logger import EventLogger
from infinitecontex.evidence_review.models import (
    ActorType,
    Confidence,
    EvidenceReview,
    EvidenceReviewDecision,
    ReviewDecisionValue,
    ReviewOutcome,
)
from infinitecontex.evidence_review.service import EvidenceReviewService
from infinitecontex.evidence_review.store import EvidenceReviewStore
from infinitecontex.planning.models import (
    AcceptanceCriterion,
    CriterionStatus,
    EvidenceType,
    PlannerProvenance,
    PlanRevision,
    Task,
    TaskStatus,
)
from infinitecontex.planning.normalization import sha256_payload
from infinitecontex.planning.service import PlanningService, _revision_to_input, _task_to_input
from infinitecontex.planning.store import PlanStore
from infinitecontex.planning.transitions import validate_transition
from infinitecontex.state_transitions.errors import (
    TransitionApprovalError,
    TransitionCriterionError,
    TransitionDependencyError,
    TransitionPlanError,
    TransitionProposalError,
    TransitionReviewError,
    TransitionTaskError,
)
from infinitecontex.state_transitions.models import (
    CriterionStatusChange,
    CriterionTransitionEntry,
    CriterionTransitionRequest,
    EligibilityOutcome,
    StateTransitionApplication,
    StateTransitionApproval,
    StateTransitionProposal,
    TransitionDecision,
)
from infinitecontex.state_transitions.policy import MAX_CRITERIA_TRANSITIONS, TransitionPolicy, model_fingerprint
from infinitecontex.state_transitions.store import StateTransitionStore
from infinitecontex.task_context.repository import RepositoryInventoryService


class StateTransitionService:
    def __init__(
        self,
        plan_store: PlanStore,
        review_store: EvidenceReviewStore,
        store: StateTransitionStore,
        *,
        planning: PlanningService | None = None,
        review_service: EvidenceReviewService | None = None,
        inventory: RepositoryInventoryService | None = None,
        event_logger: EventLogger | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.plan_store = plan_store
        self.review_store = review_store
        self.store = store
        self.planning = planning or PlanningService(plan_store)
        self.review_service = review_service
        self.inventory = inventory or RepositoryInventoryService()
        self.event_logger = event_logger
        self.clock = clock or (lambda: datetime.now(UTC))
        self.policy = TransitionPolicy()

    def propose(
        self,
        root: Path,
        plan_id: str,
        task_id: str,
        requests: Iterable[CriterionTransitionRequest],
        *,
        task_target: TaskStatus | None = None,
        revision: int | None = None,
    ) -> StateTransitionProposal:
        current = self.plan_store.load_current(plan_id)
        if revision is not None and current.current_revision != revision:
            raise TransitionPlanError("Current plan revision changed")
        task = self._task(current, task_id)
        normalized = tuple(sorted(requests, key=lambda x: x.criterion_id))
        if len(normalized) > MAX_CRITERIA_TRANSITIONS:
            raise TransitionProposalError("Too many criterion transitions")
        if len({x.criterion_id for x in normalized}) != len(normalized):
            raise TransitionCriterionError("Duplicate criterion transition")
        entries = []
        review_ids = []
        review_fps = []
        decision_ids = []
        decision_fps = []
        snapshots: list[str] = []
        evidence: list[str] = []
        executions: list[str] = []
        warnings: list[str] = []
        criterion_by_id = {x.criterion_id: x for x in task.acceptance_criteria}
        for request in normalized:
            criterion = criterion_by_id.get(request.criterion_id)
            if criterion is None:
                raise TransitionCriterionError(f"Criterion {request.criterion_id} was not found")
            if criterion.status == request.target_status:
                raise TransitionCriterionError("Transition is a semantic no-op")
            try:
                self.policy.validate_criterion(criterion.status, request.target_status)
            except ValueError as exc:
                raise TransitionCriterionError(str(exc)) from exc
            review = None
            decision = None
            human_only = criterion.required_evidence_type == EvidenceType.HUMAN_APPROVAL
            needs_review = request.target_status in {CriterionStatus.SATISFIED, CriterionStatus.FAILED}
            if needs_review and not human_only:
                if not request.review_id or not request.decision_id:
                    raise TransitionReviewError("Accepted evidence review is required")
                review, decision = self._accepted_review(
                    root, current, task, criterion, request.review_id, request.decision_id
                )
                if request.target_status == CriterionStatus.SATISFIED:
                    if (
                        not review.evidence_supports_criterion
                        or review.outcome not in self.policy.accepted_support_outcomes
                    ):
                        raise TransitionReviewError("Review does not support criterion satisfaction")
                    if review.confidence not in {Confidence.HIGH, Confidence.MEDIUM}:
                        raise TransitionReviewError("Review confidence is insufficient")
                    if review.warnings and not request.warnings_acknowledged:
                        raise TransitionReviewError("Review warnings require acknowledgement")
                elif review.outcome != ReviewOutcome.FAILED:
                    raise TransitionReviewError("Criterion failure requires an accepted failing review")
            elif needs_review and human_only:
                if not request.reason:
                    raise TransitionCriterionError("Human-only verification requires a reason")
            if (
                request.target_status
                in {CriterionStatus.WAIVED, CriterionStatus.NOT_APPLICABLE, CriterionStatus.PENDING}
                and not request.reason
            ):
                raise TransitionCriterionError("Waiver, not-applicable, and reopening transitions require a reason")
            entry0 = CriterionTransitionEntry(
                criterion_id=criterion.criterion_id,
                criterion_fingerprint=model_fingerprint(criterion),
                current_status=criterion.status,
                proposed_status=request.target_status,
                transition_rule=f"{criterion.status.value}->{request.target_status.value}",
                review_id=review.review_id if review else None,
                review_fingerprint=review.semantic_fingerprint if review else None,
                decision_id=decision.decision_id if decision else None,
                decision_fingerprint=decision.decision_fingerprint if decision else None,
                review_outcome=review.outcome if review else None,
                confidence=review.confidence if review else None,
                human_only_verification=human_only,
                mandatory=criterion.mandatory,
                reason=request.reason,
                warnings=review.warnings if review else (),
                entry_fingerprint="0" * 64,
            )
            entry = entry0.model_copy(update={"entry_fingerprint": model_fingerprint(entry0, "entry_fingerprint")})
            entries.append(entry)
            if review and decision:
                review_ids.append(review.review_id)
                review_fps.append(review.semantic_fingerprint)
                decision_ids.append(decision.decision_id)
                decision_fps.append(decision.decision_fingerprint)
                snapshots.extend(review.repository_snapshot_fingerprints)
                evidence.extend(review.evidence_ids)
                executions.extend(review.validation_execution_ids)
                warnings.extend(review.warnings)
        if task_target is not None:
            try:
                validate_transition(task.status, task_target)
            except ValueError as exc:
                raise TransitionTaskError(str(exc)) from exc
        preview = self._preview(current, task, tuple(entries), task_target)
        updated_task = next(x for x in preview.tasks if x.task_id == task.task_id)
        if task_target == TaskStatus.COMPLETED:
            incomplete = [
                x.criterion_id
                for x in updated_task.acceptance_criteria
                if x.mandatory and x.status not in {CriterionStatus.SATISFIED, CriterionStatus.WAIVED}
            ]
            if incomplete:
                raise TransitionTaskError("Mandatory criteria remain incomplete: " + ", ".join(incomplete[:10]))
            by_id = {x.task_id: x for x in current.tasks}
            blocked = [
                x for x in task.dependency_ids if by_id[x].status not in {TaskStatus.COMPLETED, TaskStatus.SUPERSEDED}
            ]
            if blocked:
                raise TransitionDependencyError("Blocking dependencies remain: " + ", ".join(blocked[:10]))
        report = self.planning._report(preview)
        if not report.valid:
            raise TransitionProposalError("Resulting plan or DAG is invalid")
        before = sha256_payload(
            {"task": task.task_fingerprint, "criteria": [model_fingerprint(x) for x in task.acceptance_criteria]}
        )
        after = sha256_payload(
            {
                "task": updated_task.task_fingerprint,
                "criteria": [model_fingerprint(x) for x in updated_task.acceptance_criteria],
            }
        )
        analysis_fingerprints = {
            self.review_store.load_review(plan_id, review_id).task_context_analysis_fingerprint
            for review_id in review_ids
        } - {None}
        if len(analysis_fingerprints) > 1:
            raise TransitionReviewError("Accepted reviews use conflicting task-context analyses")
        provisional = StateTransitionProposal(
            proposal_id="state-transition-proposal-" + "0" * 24,
            semantic_fingerprint="0" * 64,
            transition_policy_fingerprint=self.policy.fingerprint,
            plan_id=plan_id,
            source_plan_revision=current.current_revision,
            source_revision_fingerprint=current.revision_fingerprint,
            source_graph_fingerprint=current.graph_fingerprint,
            task_id=task.task_id,
            task_fingerprint=task.task_fingerprint,
            source_task_status=task.status,
            proposed_task_status=task_target,
            criterion_transitions=tuple(entries),
            accepted_review_ids=tuple(review_ids),
            accepted_review_fingerprints=tuple(review_fps),
            review_decision_ids=tuple(decision_ids),
            review_decision_fingerprints=tuple(decision_fps),
            repository_snapshot_fingerprints=tuple(sorted(set(snapshots))),
            validation_evidence_ids=tuple(sorted(set(evidence))),
            validation_execution_ids=tuple(sorted(set(executions))),
            before_state_fingerprint=before,
            proposed_after_state_fingerprint=after,
            resulting_graph_fingerprint_preview=preview.graph_fingerprint,
            resulting_revision=current.current_revision + 1,
            eligibility=EligibilityOutcome.ELIGIBLE_WITH_WARNING if warnings else EligibilityOutcome.ELIGIBLE,
            validation_checks=("plan_current", "task_current", "criteria_legal", "reviews_accepted", "dag_valid"),
            warnings=tuple(sorted(set(warnings))),
            created_at=self.clock(),
        )
        fp = model_fingerprint(provisional, "semantic_fingerprint", "proposal_id", "created_at")
        value = provisional.model_copy(
            update={"semantic_fingerprint": fp, "proposal_id": "state-transition-proposal-" + fp[:24]}
        )
        self.store.save_proposal(value)
        self._event("state_transition_proposal_persisted", {"proposal_id": value.proposal_id})
        return value

    def decide(
        self,
        plan_id: str,
        proposal_id: str,
        actor: str,
        decision: TransitionDecision,
        *,
        reason: str,
        actor_type: ActorType = ActorType.HUMAN,
        warnings_acknowledged: bool = False,
    ) -> StateTransitionApproval:
        proposal = self.store.load_proposal(plan_id, proposal_id)
        self._assert_proposal_current(proposal)
        if actor_type not in self.policy.accepted_actor_types:
            raise TransitionApprovalError("Only human review authorities may decide")
        if proposal.warnings and not warnings_acknowledged:
            raise TransitionApprovalError("Proposal warnings require acknowledgement")
        provisional = StateTransitionApproval(
            approval_id="state-transition-approval-" + "0" * 24,
            approval_fingerprint="0" * 64,
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.semantic_fingerprint,
            plan_id=plan_id,
            source_plan_revision=proposal.source_plan_revision,
            task_id=proposal.task_id,
            task_fingerprint=proposal.task_fingerprint,
            criterion_fingerprints=tuple(x.criterion_fingerprint for x in proposal.criterion_transitions),
            review_decision_fingerprints=proposal.review_decision_fingerprints,
            proposed_after_state_fingerprint=proposal.proposed_after_state_fingerprint,
            actor=actor,
            actor_type=actor_type,
            decision=decision,
            reason=reason,
            warnings_acknowledged=warnings_acknowledged,
            decided_at=self.clock(),
        )
        fp = model_fingerprint(provisional, "approval_fingerprint", "approval_id", "decided_at")
        value = provisional.model_copy(
            update={"approval_fingerprint": fp, "approval_id": "state-transition-approval-" + fp[:24]}
        )
        self.store.save_approval(value)
        self._event("state_transition_approval_recorded", {"proposal_id": proposal_id, "decision": decision.value})
        return value

    def apply(self, root: Path, plan_id: str, proposal_id: str) -> StateTransitionApplication:
        prior = self.store.list_applications(plan_id, proposal_id=proposal_id)
        if prior:
            return prior[0]
        proposal = self.store.load_proposal(plan_id, proposal_id)
        self._assert_proposal_current(proposal)
        approvals = self.store.list_approvals(plan_id, proposal_id=proposal_id)
        if not approvals:
            raise TransitionApprovalError("Approved transition proposal is required")
        approval = approvals[0]
        if approval.decision != TransitionDecision.APPROVED:
            raise TransitionApprovalError("Transition proposal was not approved")
        if approval.proposal_fingerprint != proposal.semantic_fingerprint:
            raise TransitionApprovalError("Approval fingerprint mismatch")
        current = self.plan_store.load_current(plan_id)
        task = self._task(current, proposal.task_id)
        for entry in proposal.criterion_transitions:
            criterion = next((x for x in task.acceptance_criteria if x.criterion_id == entry.criterion_id), None)
            if criterion is None or model_fingerprint(criterion) != entry.criterion_fingerprint:
                raise TransitionProposalError("Criterion changed after proposal")
            if entry.review_id and entry.decision_id:
                self._accepted_review(root, current, task, criterion, entry.review_id, entry.decision_id)
        resulting = self._preview(current, task, proposal.criterion_transitions, proposal.proposed_task_status)
        if resulting.graph_fingerprint != proposal.resulting_graph_fingerprint_preview:
            raise TransitionProposalError("Resulting graph fingerprint changed")
        plan_input = _revision_to_input(resulting, tuple(_task_to_input(x, status=x.status) for x in resulting.tasks))
        revision, report, persisted = self.planning.import_plan(
            plan_input,
            revision_reason=f"Approved G6 transition {proposal.proposal_id}",
            revision_author=PlannerProvenance.DETERMINISTIC_TRANSFORMATION,
        )
        if not persisted or not report.valid or revision.current_revision != proposal.resulting_revision:
            raise TransitionProposalError("Exactly one new revision was not created")
        new_task = self._task(revision, task.task_id)
        changes = tuple(
            CriterionStatusChange(criterion_id=x.criterion_id, before=x.current_status, after=x.proposed_status)
            for x in proposal.criterion_transitions
        )
        provisional = StateTransitionApplication(
            application_id="state-transition-application-" + "0" * 24,
            application_fingerprint="0" * 64,
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.semantic_fingerprint,
            approval_id=approval.approval_id,
            approval_fingerprint=approval.approval_fingerprint,
            plan_id=plan_id,
            source_revision=proposal.source_plan_revision,
            resulting_revision=revision.current_revision,
            source_revision_fingerprint=proposal.source_revision_fingerprint,
            resulting_revision_fingerprint=revision.revision_fingerprint,
            actor=approval.actor,
            task_id=task.task_id,
            task_status_before=task.status,
            task_status_after=new_task.status,
            criterion_changes=changes,
            review_ids=proposal.accepted_review_ids,
            before_state_fingerprint=proposal.before_state_fingerprint,
            after_state_fingerprint=proposal.proposed_after_state_fingerprint,
            resulting_graph_fingerprint=revision.graph_fingerprint,
            applied_at=self.clock(),
        )
        fp = model_fingerprint(provisional, "application_fingerprint", "application_id", "applied_at")
        value = provisional.model_copy(
            update={"application_fingerprint": fp, "application_id": "state-transition-application-" + fp[:24]}
        )
        self.store.save_application(value)
        self._event("state_transition_applied", {"application_id": value.application_id})
        return value

    def _preview(
        self,
        current: PlanRevision,
        task: Task,
        entries: tuple[CriterionTransitionEntry, ...],
        task_target: TaskStatus | None,
    ) -> PlanRevision:
        targets = {x.criterion_id: x.proposed_status for x in entries}
        criteria = tuple(
            x.model_copy(update={"status": targets.get(x.criterion_id, x.status)}) for x in task.acceptance_criteria
        )
        inputs = []
        for item in current.tasks:
            inp = _task_to_input(
                item, status=task_target if item.task_id == task.task_id and task_target else item.status
            )
            if item.task_id == task.task_id:
                inp = inp.model_copy(update={"acceptance_criteria": criteria})
            inputs.append(inp)
        plan_input = _revision_to_input(current, tuple(inputs))
        return self.planning._build_revision(
            plan_input,
            previous=current,
            reason="G6 transition preview",
            author=PlannerProvenance.DETERMINISTIC_TRANSFORMATION,
        )

    def _accepted_review(
        self,
        root: Path,
        plan: PlanRevision,
        task: Task,
        criterion: AcceptanceCriterion,
        review_id: str,
        decision_id: str,
    ) -> tuple[EvidenceReview, EvidenceReviewDecision]:
        review = self.review_store.load_review(plan.plan_id, review_id)
        decision = self.review_store.load_decision(plan.plan_id, decision_id)
        if self.review_service:
            self.review_service.assert_fresh(review)
            for contribution in review.contributions:
                record = self.review_service.validation_store.load_record(contribution.execution_id)
                if record.record_fingerprint != contribution.execution_fingerprint:
                    raise TransitionReviewError("Validation execution changed after evidence review")
            if review.task_context_analysis_fingerprint and self.review_service.analysis_store:
                analysis = self.review_service.analysis_store.load_current(
                    plan.plan_id, plan.current_revision, task.task_id
                )
                if analysis.semantic_fingerprint != review.task_context_analysis_fingerprint:
                    raise TransitionReviewError("Task-context analysis changed after evidence review")
        if (
            review.plan_revision != plan.current_revision
            or review.task_fingerprint != task.task_fingerprint
            or review.criterion_id != getattr(criterion, "criterion_id")
        ):
            raise TransitionReviewError("Accepted review is stale or linked elsewhere")
        if (
            decision.review_id != review.review_id
            or decision.review_fingerprint != review.semantic_fingerprint
            or decision.decision != ReviewDecisionValue.ACCEPTED
            or decision.actor_type not in self.policy.accepted_actor_types
        ):
            raise TransitionReviewError("Exact accepted human review decision is required")
        current_snapshot = self.inventory.build(root.resolve(strict=True)).snapshot.semantic_fingerprint
        if review.repository_snapshot_fingerprints and any(
            x != current_snapshot for x in review.repository_snapshot_fingerprints
        ):
            raise TransitionReviewError("Review repository snapshot is stale")
        return review, decision

    def _assert_proposal_current(self, proposal: StateTransitionProposal) -> None:
        if proposal.transition_policy_fingerprint != self.policy.fingerprint:
            raise TransitionProposalError("Transition policy changed")
        current = self.plan_store.load_current(proposal.plan_id)
        if (
            current.current_revision != proposal.source_plan_revision
            or current.revision_fingerprint != proposal.source_revision_fingerprint
            or current.graph_fingerprint != proposal.source_graph_fingerprint
        ):
            raise TransitionPlanError("Transition proposal is stale")
        if self._task(current, proposal.task_id).task_fingerprint != proposal.task_fingerprint:
            raise TransitionTaskError("Task changed after proposal")

    @staticmethod
    def _task(plan: PlanRevision, task_id: str) -> Task:
        task = next((x for x in plan.tasks if x.task_id == task_id), None)
        if task is None:
            raise TransitionTaskError(f"Task {task_id} was not found")
        return task

    def _event(self, name: str, payload: dict[str, object]) -> None:
        if self.event_logger:
            self.event_logger.log(name, payload)
