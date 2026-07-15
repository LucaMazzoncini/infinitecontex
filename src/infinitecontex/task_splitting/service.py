"""Deterministic bounded splitting with fail-closed fit and human application."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from infinitecontex.planning.models import (
    ContextRequirements,
    PlanInput,
    PlannerProvenance,
    PlanRevision,
    Task,
    TaskInput,
    TaskStatus,
    TaskType,
)
from infinitecontex.planning.normalization import sha256_payload
from infinitecontex.planning.service import PlanningService
from infinitecontex.task_context.models import TaskContextAnalysis, TaskContextDecision
from infinitecontex.task_context.service import TaskContextService
from infinitecontex.task_splitting.errors import (
    SplitApprovalError,
    SplitBoundsError,
    SplitEligibilityError,
    SplitProposalStaleError,
)
from infinitecontex.task_splitting.fingerprints import approval_fingerprint, proposal_fingerprint
from infinitecontex.task_splitting.models import (
    ApplicationStatus,
    ApprovalActorType,
    ApprovalDecision,
    ContractCoverageReport,
    CoverageDisposition,
    CoverageRecord,
    DependencyRewrite,
    ProposedChild,
    RuleCandidate,
    SplitApproval,
    SplitEligibility,
    SplitProposal,
    SplitRule,
)
from infinitecontex.task_splitting.policy import SplitPolicy
from infinitecontex.task_splitting.store import TaskSplitStore

_PASSING = {
    TaskContextDecision.FITS_TARGET,
    TaskContextDecision.FITS_WITH_WARNING,
    TaskContextDecision.FITS_HARD_LIMIT,
}
_PARTITION_FIELDS = (
    "required_files",
    "required_symbols",
    "required_tests",
    "required_documentation",
    "path_references",
    "symbol_references",
)


class TaskSplittingService:
    def __init__(
        self,
        planning: PlanningService,
        context: TaskContextService,
        store: TaskSplitStore,
        policy: SplitPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.planning = planning
        self.context = context
        self.store = store
        self.policy = policy or SplitPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))

    def propose(
        self,
        plan_id: str,
        task_id: str,
        repository_root: Path,
        *,
        model_name: str | None = None,
        digest: str | None = None,
        persist: bool = True,
    ) -> SplitProposal:
        source = self.planning.store.load_current(plan_id)
        task = _task_by_id(source, task_id)
        analysis = self.context.context_fit(
            plan_id,
            repository_root,
            model_name=model_name,
            digest=digest,
            task_id=task_id,
            persist=False,
        )[0]
        if self.context.staleness(analysis, repository_root).stale:
            raise SplitProposalStaleError("Task analysis is stale; rerun context fit and propose again")
        optional = analysis.decision in _PASSING
        if optional and not self.policy.allow_optional_fitting_proposals:
            raise SplitEligibilityError("Task already fits and optional split proposals are disabled")
        if not optional and analysis.decision != TaskContextDecision.SPLIT_REQUIRED:
            raise SplitEligibilityError(
                f"Task cannot be split while fit decision is {analysis.decision.value}; resolve that blocker first"
            )

        inputs = list(_revision_inputs(source))
        source_input = _input_by_key(inputs, task.task_key)
        depths: dict[str, int] = {}
        parents: dict[str, str] = {}
        rules: dict[str, SplitRule] = {}
        barrier_keys: set[str] = set()
        inputs, created = self._split_once(inputs, source_input, 1)
        barrier_keys.add(source_input.task_key)
        for child in created:
            depths[child.task_key] = 1
            parents[child.task_key] = task.task_id
            rules[child.task_key] = SplitRule.REQUIRED_CONTEXT_GROUP

        transient, fits = self._fit_inputs(source, inputs, repository_root, model_name, digest, barrier_keys)
        while True:
            oversized = [
                item for item in fits if item.decision == TaskContextDecision.SPLIT_REQUIRED
            ]
            if not oversized:
                break
            target_analysis = sorted(oversized, key=lambda item: item.task_id)[0]
            target = _task_by_id(transient, target_analysis.task_id)
            target_depth = depths[target.task_key]
            if target_depth >= self.policy.maximum_depth:
                raise SplitBoundsError(
                    f"Task {target.task_key} still exceeds context at split depth "
                    f"{target_depth}; narrow its required evidence"
                )
            target_input = _input_by_key(inputs, target.task_key)
            inputs, created = self._split_once(inputs, target_input, target_depth + 1)
            barrier_keys.add(target.task_key)
            for child in created:
                depths[child.task_key] = target_depth + 1
                parents[child.task_key] = target.task_id
                rules[child.task_key] = SplitRule.REQUIRED_CONTEXT_GROUP
            if len(depths) > self.policy.maximum_descendants:
                raise SplitBoundsError("Recursive split exceeded the descendant limit; narrow the source task")
            transient, fits = self._fit_inputs(
                source, inputs, repository_root, model_name, digest, barrier_keys
            )

        failures = [item for item in fits if item.decision not in _PASSING]
        if failures:
            first = sorted(failures, key=lambda item: item.task_id)[0]
            raise SplitEligibilityError(
                f"Proposed leaf {first.task_id} is blocked by {first.decision.value}; correct its declarations"
            )
        fit_by_id = {item.task_id: item for item in fits}
        leaves = tuple(
            item for item in transient.tasks if item.task_key in depths and item.task_key not in barrier_keys
        )
        children = tuple(
            ProposedChild(
                proposed_task_id=item.task_id,
                stable_child_key=item.task_key,
                parent_source_task_id=parents[item.task_key],
                split_depth=depths[item.task_key],
                split_dimension=rules[item.task_key],
                split_partition_key=str(item.metadata["split_partition_key"]),
                task=_task_input(item, transient),
                deterministic_fingerprint=item.task_fingerprint,
                context_fit=fit_by_id[item.task_id],
                requested_capabilities=item.requested_capabilities,
                granted_capabilities=(),
            )
            for item in sorted(leaves, key=lambda value: value.task_key)
        )
        proposed_plan = _plan_input(transient)
        coverage = _coverage(task, children)
        rewrites = _dependency_rewrites(source, task, children)
        now = self.clock()
        provisional = SplitProposal(
            proposal_id="split-proposal-" + "0" * 24,
            semantic_fingerprint="0" * 64,
            split_policy_id=self.policy.policy_id,
            split_policy_version=self.policy.version,
            plan_id=source.plan_id,
            source_plan_revision=source.current_revision,
            source_revision_fingerprint=source.revision_fingerprint,
            source_graph_fingerprint=source.graph_fingerprint,
            source_task_id=task.task_id,
            source_task_fingerprint=task.task_fingerprint,
            repository_snapshot_fingerprint=analysis.repository_snapshot_fingerprint,
            profile_id=analysis.profile_id,
            model_digest=analysis.model_digest,
            originating_analysis_id=analysis.analysis_id,
            originating_analysis_fingerprint=analysis.semantic_fingerprint,
            eligibility=(
                SplitEligibility.ELIGIBLE_WITH_WARNINGS if optional else SplitEligibility.ELIGIBLE
            ),
            optional_proposal=optional,
            selected_rule=SplitRule.REQUIRED_CONTEXT_GROUP,
            rule_candidates=(
                RuleCandidate(
                    rule=SplitRule.REQUIRED_CONTEXT_GROUP,
                    priority=1,
                    eligible=True,
                    partition_count=len(children),
                    reason="Required context declarations form independently validated evidence groups.",
                ),
            ),
            proposed_children=children,
            completion_barrier_key=task.task_key,
            dependency_rewrites=rewrites,
            contract_coverage=coverage,
            shared_contract_fields=("acceptance_criteria", "required_evidence", "forbidden_scopes"),
            shared_context_duplicate_tokens=_duplicate_tokens(tuple(fit_by_id.values())),
            proposed_plan=proposed_plan,
            resulting_graph_fingerprint=transient.graph_fingerprint,
            resulting_task_count=transient.task_count,
            total_leaf_tasks=len(children),
            maximum_split_depth=max(depths.values()),
            every_leaf_fits=True,
            validation_passed=True,
            warnings=(("Source task already fits; this split is optional.",) if optional else ()),
            errors=(),
            remediation=(),
            created_at=now,
        )
        fingerprint = proposal_fingerprint(provisional)
        proposal = provisional.model_copy(
            update={
                "proposal_id": f"split-proposal-{fingerprint[:24]}",
                "semantic_fingerprint": fingerprint,
            }
        )
        if persist:
            self.store.save_proposal(proposal)
        return proposal

    def approve_and_apply(
        self,
        plan_id: str,
        proposal_id: str,
        repository_root: Path,
        *,
        actor_identifier: str,
        decision_reason: str,
        warnings_acknowledged: bool = False,
    ) -> tuple[PlanRevision, SplitApproval]:
        proposal = self.store.load_proposal(plan_id, proposal_id)
        current = self.planning.store.load_current(plan_id)
        if (
            current.current_revision != proposal.source_plan_revision
            or current.revision_fingerprint != proposal.source_revision_fingerprint
            or current.graph_fingerprint != proposal.source_graph_fingerprint
        ):
            raise SplitProposalStaleError("Plan changed after the split proposal; recreate the proposal")
        snapshot = self.context.inventory_service.build(repository_root).snapshot
        if snapshot.semantic_fingerprint != proposal.repository_snapshot_fingerprint:
            raise SplitProposalStaleError("Repository changed after the split proposal; recreate the proposal")
        if proposal.warnings and not warnings_acknowledged:
            raise SplitApprovalError("Proposal has warnings; review and explicitly acknowledge them")
        revision, report, persisted = self.planning.import_plan(
            proposal.proposed_plan,
            revision_reason=f"Approved split proposal {proposal.proposal_id}",
            revision_author=PlannerProvenance.HUMAN_AUTHORED,
        )
        if not persisted or not report.valid:
            raise SplitApprovalError("Approved split did not create a valid new plan revision")
        provisional = SplitApproval(
            approval_id="split-approval-" + "0" * 24,
            approval_fingerprint="0" * 64,
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.semantic_fingerprint,
            plan_id=plan_id,
            source_revision=proposal.source_plan_revision,
            proposed_graph_fingerprint=proposal.resulting_graph_fingerprint,
            decision=ApprovalDecision.APPROVED,
            actor_identifier=actor_identifier,
            actor_type=ApprovalActorType.HUMAN,
            decision_reason=decision_reason,
            decided_at=self.clock(),
            repository_snapshot_fingerprint=proposal.repository_snapshot_fingerprint,
            profile_id=proposal.profile_id,
            model_digest=proposal.model_digest,
            warnings_acknowledged=warnings_acknowledged,
            applied_revision_number=revision.current_revision,
            application_status=ApplicationStatus.APPLIED,
        )
        fingerprint = approval_fingerprint(provisional)
        approval = provisional.model_copy(
            update={
                "approval_id": f"split-approval-{fingerprint[:24]}",
                "approval_fingerprint": fingerprint,
            }
        )
        self.store.save_approval(approval)
        return revision, approval

    def _split_once(
        self, inputs: list[TaskInput], parent: TaskInput, depth: int
    ) -> tuple[list[TaskInput], tuple[TaskInput, ...]]:
        units = _partition_units(parent.context_requirements)
        if len(units) < self.policy.minimum_partitions:
            raise SplitEligibilityError(
                f"Task {parent.task_key} has no safe structural split; narrow a whole-file or symbol declaration"
            )
        groups = _bounded_groups(units, self.policy.maximum_direct_children)
        children = tuple(_child(parent, group, index, depth) for index, group in enumerate(groups, 1))
        barrier = parent.model_copy(
            update={
                "task_type": TaskType.INTEGRATION,
                "status": TaskStatus.DRAFT,
                "objective": "Verify completion of all context-bounded child tasks.",
                "description": "Deterministic completion barrier created by bounded task splitting.",
                "dependency_keys": tuple(child.task_key for child in children),
                "soft_dependency_keys": (),
                "context_requirements": ContextRequirements(),
                "requested_capabilities": (),
                "granted_capabilities": (),
                "provenance": PlannerProvenance.DETERMINISTIC_TRANSFORMATION,
                "metadata": {**parent.metadata, "split_barrier": True, "split_depth": depth},
            }
        )
        replaced = [item for item in inputs if item.task_key != parent.task_key]
        replaced.extend((barrier, *children))
        return replaced, children

    def _fit_inputs(
        self,
        source: PlanRevision,
        inputs: list[TaskInput],
        repository_root: Path,
        model_name: str | None,
        digest: str | None,
        barrier_keys: set[str],
    ) -> tuple[PlanRevision, tuple[TaskContextAnalysis, ...]]:
        transient, report = self.planning.validate(_source_plan_input(source, tuple(inputs)))
        if not report.valid:
            raise SplitEligibilityError("Deterministic split produced an invalid task DAG")
        leaf_ids = tuple(
            task.task_id for task in transient.tasks if task.task_key not in barrier_keys
        )
        fits = self.context.context_fit_revision(
            transient,
            repository_root,
            model_name=model_name,
            digest=digest,
            task_ids=leaf_ids,
            persist=False,
        )
        return transient, fits


def _partition_units(context: ContextRequirements) -> tuple[tuple[str, Any], ...]:
    return tuple(
        (field, value)
        for field in _PARTITION_FIELDS
        for value in getattr(context, field)
        if getattr(value, "requirement", "required") == "required"
    )


def _bounded_groups(
    units: tuple[tuple[str, Any], ...], maximum: int
) -> tuple[tuple[tuple[str, Any], ...], ...]:
    count = min(len(units), maximum)
    return tuple(tuple(units[index::count]) for index in range(count))


def _child(
    parent: TaskInput, units: tuple[tuple[str, Any], ...], index: int, depth: int
) -> TaskInput:
    context_payload = parent.context_requirements.model_dump(mode="python")
    for field in _PARTITION_FIELDS:
        context_payload[field] = tuple(value for name, value in units if name == field)
    partition = sha256_payload([(name, _json_value(value)) for name, value in units])
    key = f"{parent.task_key[:135]}.part-{index:02d}-{partition[:8]}"
    return parent.model_copy(
        update={
            "task_id": None,
            "task_key": key,
            "title": f"{parent.title} — part {index}",
            "dependency_keys": parent.dependency_keys,
            "soft_dependency_keys": (),
            "parent_task_key": parent.task_key,
            "context_requirements": ContextRequirements.model_validate(context_payload),
            "granted_capabilities": (),
            "provenance": PlannerProvenance.DETERMINISTIC_TRANSFORMATION,
            "metadata": {
                **parent.metadata,
                "split_depth": depth,
                "split_partition_key": partition,
            },
        }
    )


def _json_value(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _revision_inputs(revision: PlanRevision) -> tuple[TaskInput, ...]:
    return tuple(_task_input(task, revision) for task in revision.tasks)


def _task_input(task: Task, revision: PlanRevision) -> TaskInput:
    keys = {item.task_id: item.task_key for item in revision.tasks}
    payload = task.model_dump(
        mode="python",
        exclude={
            "plan_id",
            "dependency_ids",
            "soft_dependency_ids",
            "parent_task_id",
            "created_at",
            "updated_at",
            "task_fingerprint",
        },
    )
    payload.update(
        dependency_keys=tuple(keys[value] for value in task.dependency_ids),
        soft_dependency_keys=tuple(keys[value] for value in task.soft_dependency_ids),
        parent_task_key=(keys[task.parent_task_id] if task.parent_task_id is not None else None),
    )
    return TaskInput.model_validate(payload)


def _source_plan_input(source: PlanRevision, tasks: tuple[TaskInput, ...]) -> PlanInput:
    return PlanInput(
        plan_id=source.plan_id,
        stable_plan_key=source.stable_plan_key,
        title=source.title,
        objective=source.objective,
        status=source.status,
        planner_provenance=PlannerProvenance.DETERMINISTIC_TRANSFORMATION,
        parent_plan_id=source.parent_plan_id,
        originating_request_ref=source.originating_request_ref,
        repository_ref=source.repository_ref,
        target_branch=source.target_branch,
        model_profile_ref=source.model_profile_ref,
        planning_policy_version=source.planning_policy_version,
        warnings=source.warnings,
        metadata=source.metadata,
        tasks=tasks,
    )


def _plan_input(revision: PlanRevision) -> PlanInput:
    return _source_plan_input(revision, _revision_inputs(revision))


def _task_by_id(revision: PlanRevision, task_id: str) -> Task:
    task = next((item for item in revision.tasks if item.task_id == task_id), None)
    if task is None:
        raise SplitEligibilityError(f"Task {task_id} does not belong to plan {revision.plan_id}")
    return task


def _input_by_key(inputs: list[TaskInput], key: str) -> TaskInput:
    return next(item for item in inputs if item.task_key == key)


def _coverage(task: Task, children: tuple[ProposedChild, ...]) -> ContractCoverageReport:
    child_keys = tuple(item.stable_child_key for item in children)
    records = tuple(
        CoverageRecord(
            category=category,
            source_key=key,
            disposition=CoverageDisposition.SHARED,
            child_keys=child_keys,
            detail="Retained on the completion barrier and copied to validated leaves.",
        )
        for category, values in (
            ("acceptance_criterion", task.acceptance_criteria),
            ("required_evidence", task.required_evidence),
            ("expected_output", task.expected_outputs),
            ("forbidden_scope", task.forbidden_scopes),
        )
        for value in values
        for key in (_contract_key(value),)
    )
    return ContractCoverageReport(
        records=records,
        complete=True,
        unresolved_count=0,
        shared_record_count=len(records),
    )


def _contract_key(value: Any) -> str:
    for field in ("criterion_id", "evidence_id", "name"):
        if hasattr(value, field):
            return str(getattr(value, field))
    return str(value)


def _dependency_rewrites(
    source: PlanRevision, task: Task, children: tuple[ProposedChild, ...]
) -> tuple[DependencyRewrite, ...]:
    child_keys = tuple(item.stable_child_key for item in children)
    incoming = tuple(
        DependencyRewrite(
            dependent_task_key=child.stable_child_key,
            old_dependency_key=task.task_key,
            new_dependency_keys=tuple(
                item.task_key for item in source.tasks if item.task_id in task.dependency_ids
            ),
            kind="incoming",
        )
        for child in children
    )
    outgoing = tuple(
        DependencyRewrite(
            dependent_task_key=item.task_key,
            old_dependency_key=task.task_key,
            new_dependency_keys=(task.task_key,),
            kind="unchanged",
        )
        for item in source.tasks
        if task.task_id in item.dependency_ids
    )
    barrier = DependencyRewrite(
        dependent_task_key=task.task_key,
        old_dependency_key=task.task_key,
        new_dependency_keys=child_keys,
        kind="internal",
    )
    return (*incoming, barrier, *outgoing)


def _duplicate_tokens(analyses: tuple[TaskContextAnalysis, ...]) -> int:
    seen: set[str] = set()
    duplicate = 0
    for analysis in sorted(analyses, key=lambda item: item.task_id):
        for candidate in analysis.included_candidates:
            if candidate.candidate_fingerprint in seen:
                duplicate += candidate.token_count
            else:
                seen.add(candidate.candidate_fingerprint)
    return duplicate
