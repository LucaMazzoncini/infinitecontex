"""Deterministic G7 authorization, allowance, and controlled-dispatch service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from infinitecontex.planning.models import PlanRevision, Task, TaskStatus
from infinitecontex.planning.store import PlanStore
from infinitecontex.task_context.models import TaskContextDecision
from infinitecontex.task_context.repository import RepositoryInventoryService
from infinitecontex.task_context.store import TaskContextAnalysisStore
from infinitecontex.task_execution.errors import ActionRejected, AllowanceExceeded, AuthorizationDenied, GrantRejected
from infinitecontex.task_execution.models import (
    ActionJournalEntry,
    ActionKind,
    ActionRequest,
    ActionStatus,
    AuthorizationApproval,
    AuthorizationDecision,
    AuthorizationProposal,
    AuthorizationSpecification,
    ExecutionGrant,
    ExecutionSession,
    GrantRevocation,
    GrantState,
    GrantStateRecord,
    SessionState,
    ToolAuthorization,
)
from infinitecontex.task_execution.store import TaskExecutionStore
from infinitecontex.tools.execution_models import InvocationInput, OperationKind
from infinitecontex.tools.execution_service import NoCommandGitStateProvider, RepositoryReadExecutionService
from infinitecontex.tools.fingerprints import sha256_payload
from infinitecontex.tools.models import PolicyDecision
from infinitecontex.tools.mutation_models import MutationDecision
from infinitecontex.tools.mutation_service import RepositoryMutationService
from infinitecontex.tools.policy import ToolPolicy
from infinitecontex.tools.registry import ToolRegistry
from infinitecontex.tools.validation_models import ValidationDecision
from infinitecontex.tools.validation_service import ValidationExecutionService

_READ_TOOLS = {
    ActionKind.REPO_FILES: "repository.list-inventory",
    ActionKind.REPO_READ: "repository.read-file",
    ActionKind.REPO_READ_RANGE: "repository.read-source-range",
    ActionKind.REPO_PATH_SEARCH: "repository.search-paths",
    ActionKind.REPO_LITERAL_SEARCH: "repository.search-literal",
}
_MUTATION_TOOLS = {
    "repository.apply-source-patch",
    "repository.write-test",
    "repository.write-documentation",
}
_VALIDATION_TOOLS = {"execution.run-tests", "execution.run-build", "execution.run-static-analysis"}
_ALLOWED_TOOLS = set(_READ_TOOLS.values()) | _MUTATION_TOOLS | _VALIDATION_TOOLS
_PASSING_CONTEXT = {
    TaskContextDecision.FITS_TARGET,
    TaskContextDecision.FITS_WITH_WARNING,
    TaskContextDecision.FITS_HARD_LIMIT,
}
_READ_STATUSES = {TaskStatus.READY, TaskStatus.IN_PROGRESS, TaskStatus.AWAITING_REVIEW, TaskStatus.FAILED}
_MUTATION_STATUSES = {TaskStatus.IN_PROGRESS}
_VALIDATION_STATUSES = {TaskStatus.IN_PROGRESS, TaskStatus.AWAITING_REVIEW, TaskStatus.FAILED}


class TaskExecutionService:
    def __init__(
        self,
        registry: ToolRegistry,
        plan_store: PlanStore,
        analysis_store: TaskContextAnalysisStore,
        store: TaskExecutionStore,
        *,
        read_service: RepositoryReadExecutionService | None = None,
        mutation_service: RepositoryMutationService | None = None,
        validation_service: ValidationExecutionService | None = None,
        inventory_service: RepositoryInventoryService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry
        self.plan_store = plan_store
        self.analysis_store = analysis_store
        self.store = store
        self.read_service = read_service
        self.mutation_service = mutation_service
        self.validation_service = validation_service
        self.inventory_service = inventory_service or RepositoryInventoryService(
            git_provider=NoCommandGitStateProvider()
        )
        self.clock = clock or (lambda: datetime.now(UTC))

    def propose(
        self,
        root: Path,
        plan_id: str,
        task_id: str,
        specification: AuthorizationSpecification,
        *,
        revision: int | None = None,
    ) -> AuthorizationProposal:
        plan, task, analysis, snapshot = self._current(root, plan_id, task_id, revision)
        tools = tuple(self._tool(name, plan, task, analysis) for name in specification.allowed_tools)
        self._validate_actions(task, specification, tools)
        self._validate_scopes(task, specification)
        semantic = {
            "policy_version": 1,
            "plan_id": plan.plan_id,
            "plan_revision": plan.current_revision,
            "graph_fingerprint": plan.graph_fingerprint,
            "task_id": task.task_id,
            "task_fingerprint": task.task_fingerprint,
            "task_status": task.status.value,
            "analysis_fingerprint": analysis.semantic_fingerprint,
            "repository_snapshot_fingerprint": snapshot,
            "tools": [item.model_dump(mode="json") for item in tools],
            "specification": specification.model_dump(mode="json", exclude={"requested_actor", "reason"}),
        }
        fingerprint = sha256_payload(semantic)
        value = AuthorizationProposal(
            proposal_id=f"execution-authorization-{fingerprint[:24]}",
            semantic_fingerprint=fingerprint,
            plan_id=plan.plan_id,
            plan_revision=plan.current_revision,
            graph_fingerprint=plan.graph_fingerprint,
            task_id=task.task_id,
            task_fingerprint=task.task_fingerprint,
            task_status=task.status.value,
            requested_capabilities=tuple(sorted(x.value for x in task.requested_capabilities)),
            affected_scopes=task.affected_scopes,
            forbidden_scopes=task.forbidden_scopes,
            analysis_id=analysis.analysis_id,
            analysis_fingerprint=analysis.semantic_fingerprint,
            repository_snapshot_fingerprint=snapshot,
            tools=tools,
            allowed_actions=tuple(sorted(specification.allowed_actions)),
            read_scopes=specification.read_scopes,
            write_scopes=specification.write_scopes,
            permitted_mutation_proposals=tuple(sorted(specification.permitted_mutation_proposals)),
            permitted_validation_proposals=tuple(sorted(specification.permitted_validation_proposals)),
            maximum_total_actions=specification.maximum_total_actions,
            maximum_actions_per_tool={
                item.tool_id: specification.maximum_actions_per_tool[item.canonical_name] for item in tools
            },
            maximum_read_bytes=specification.maximum_read_bytes,
            maximum_search_bytes=specification.maximum_search_bytes,
            maximum_output_bytes=specification.maximum_output_bytes,
            maximum_mutation_actions=specification.maximum_mutation_actions,
            maximum_validation_actions=specification.maximum_validation_actions,
            repository_snapshot_policy=specification.repository_snapshot_policy,
            continuation_policy=specification.continuation_policy,
            risk="high"
            if specification.maximum_mutation_actions
            else "medium"
            if specification.maximum_validation_actions
            else "low",
            created_at=self.clock(),
        )
        existing = {item.proposal_id: item for item in self.store.list_proposals(plan_id)}.get(value.proposal_id)
        if existing is not None:
            return existing
        self.store.save_proposal(value)
        return value

    def decide(
        self,
        plan_id: str,
        proposal_id: str,
        actor: str,
        decision: AuthorizationDecision,
        *,
        reason: str,
        warnings_acknowledged: bool = False,
    ) -> AuthorizationApproval:
        proposal = self.store.load_proposal(plan_id, proposal_id)
        existing = [x for x in self.store.list_approvals(plan_id) if x.proposal_id == proposal_id]
        if existing:
            if len(existing) == 1 and existing[0].decision == decision and existing[0].actor == actor:
                return existing[0]
            raise AuthorizationDenied("Authorization proposal already has an immutable decision")
        semantic = {
            "proposal_id": proposal.proposal_id,
            "proposal_fingerprint": proposal.semantic_fingerprint,
            "actor": actor,
            "actor_type": "human",
            "decision": decision.value,
            "reason": reason,
            "warnings_acknowledged": warnings_acknowledged,
        }
        fingerprint = sha256_payload(semantic)
        value = AuthorizationApproval(
            approval_id=f"execution-approval-{fingerprint[:24]}",
            approval_fingerprint=fingerprint,
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.semantic_fingerprint,
            plan_id=proposal.plan_id,
            plan_revision=proposal.plan_revision,
            task_id=proposal.task_id,
            task_fingerprint=proposal.task_fingerprint,
            actor=actor,
            decision=decision,
            reason=reason,
            warnings_acknowledged=warnings_acknowledged,
            authorized_tools=tuple(item.tool_id for item in proposal.tools),
            authorized_scopes=tuple(sorted((*proposal.read_scopes, *proposal.write_scopes))),
            maximum_total_actions=proposal.maximum_total_actions,
            decided_at=self.clock(),
        )
        self.store.save_approval(value)
        return value

    def activate(self, root: Path, plan_id: str, proposal_id: str) -> ExecutionGrant:
        proposal = self.store.load_proposal(plan_id, proposal_id)
        existing = [x for x in self.store.list_grants(plan_id) if x.proposal_id == proposal_id]
        if existing:
            return existing[0]
        approvals = [x for x in self.store.list_approvals(plan_id) if x.proposal_id == proposal_id]
        if len(approvals) != 1 or approvals[0].decision != AuthorizationDecision.APPROVED:
            raise GrantRejected("Exact human approval is required before grant activation")
        approval = approvals[0]
        plan, task, analysis, snapshot = self._current(root, plan_id, proposal.task_id, proposal.plan_revision)
        if (
            plan.graph_fingerprint != proposal.graph_fingerprint
            or task.task_fingerprint != proposal.task_fingerprint
            or analysis.semantic_fingerprint != proposal.analysis_fingerprint
            or snapshot != proposal.repository_snapshot_fingerprint
        ):
            raise GrantRejected("Authorization proposal is stale")
        for tool in proposal.tools:
            current = self.registry.get(tool.tool_id)
            if current.definition_fingerprint != tool.definition_fingerprint:
                raise GrantRejected("Tool definition changed after authorization proposal")
        self._verify_referenced_approvals(proposal)
        semantic = {"proposal": proposal.semantic_fingerprint, "approval": approval.approval_fingerprint}
        fingerprint = sha256_payload(semantic)
        grant = ExecutionGrant(
            grant_id=f"execution-grant-{fingerprint[:24]}",
            grant_fingerprint=fingerprint,
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.semantic_fingerprint,
            approval_id=approval.approval_id,
            approval_fingerprint=approval.approval_fingerprint,
            plan_id=proposal.plan_id,
            plan_revision=proposal.plan_revision,
            task_id=proposal.task_id,
            task_fingerprint=proposal.task_fingerprint,
            task_status_at_activation=proposal.task_status,
            repository_snapshot_fingerprint=proposal.repository_snapshot_fingerprint,
            analysis_fingerprint=proposal.analysis_fingerprint,
            tools=proposal.tools,
            allowed_actions=proposal.allowed_actions,
            read_scopes=proposal.read_scopes,
            write_scopes=proposal.write_scopes,
            permitted_mutation_proposals=proposal.permitted_mutation_proposals,
            permitted_validation_proposals=proposal.permitted_validation_proposals,
            maximum_total_actions=proposal.maximum_total_actions,
            maximum_actions_per_tool=proposal.maximum_actions_per_tool,
            maximum_read_bytes=proposal.maximum_read_bytes,
            maximum_search_bytes=proposal.maximum_search_bytes,
            maximum_output_bytes=proposal.maximum_output_bytes,
            maximum_mutation_actions=proposal.maximum_mutation_actions,
            maximum_validation_actions=proposal.maximum_validation_actions,
            activated_at=self.clock(),
        )
        state = GrantStateRecord(
            grant_id=grant.grant_id,
            state=GrantState.ACTIVE,
            remaining_total_actions=grant.maximum_total_actions,
            remaining_actions_per_tool=grant.maximum_actions_per_tool,
            remaining_read_bytes=grant.maximum_read_bytes,
            remaining_search_bytes=grant.maximum_search_bytes,
            remaining_output_bytes=grant.maximum_output_bytes,
            remaining_mutation_actions=grant.maximum_mutation_actions,
            remaining_validation_actions=grant.maximum_validation_actions,
            version=1,
            updated_at=self.clock(),
        )
        self.store.save_grant(grant, state)
        return grant

    def start_session(self, plan_id: str, grant_id: str) -> ExecutionSession:
        grant = self.store.load_grant(plan_id, grant_id)
        sessions = [x for x in self.store.list_sessions(plan_id) if x.grant_id == grant_id]
        if sessions:
            return sessions[0]
        state = self.store.load_state(plan_id, grant_id)
        if state.state != GrantState.ACTIVE:
            raise GrantRejected(f"Grant is {state.state.value}")
        fingerprint = sha256_payload({"grant": grant.grant_fingerprint})
        now = self.clock()
        session = ExecutionSession(
            session_id=f"execution-session-{fingerprint[:24]}",
            session_fingerprint=fingerprint,
            grant_id=grant.grant_id,
            grant_fingerprint=grant.grant_fingerprint,
            plan_id=grant.plan_id,
            plan_revision=grant.plan_revision,
            task_id=grant.task_id,
            task_fingerprint=grant.task_fingerprint,
            state=SessionState.ACTIVE,
            initial_snapshot_fingerprint=grant.repository_snapshot_fingerprint,
            current_snapshot_fingerprint=grant.repository_snapshot_fingerprint,
            started_at=now,
            updated_at=now,
        )
        self.store.save_session(session)
        return session

    def run(self, root: Path, plan_id: str, session_id: str, request: ActionRequest) -> ActionJournalEntry:
        existing = self.store.load_action(plan_id, session_id, request.action_request_id)
        fingerprint = self._action_fingerprint(request)
        if existing:
            if existing.action_fingerprint != fingerprint:
                raise ActionRejected("Duplicate action ID has different semantic content")
            return existing
        session = self.store.load_session(plan_id, session_id)
        grant = self.store.load_grant(plan_id, session.grant_id)
        self._admit(root, session, grant, request, fingerprint)
        lock = self.store.lock(plan_id, grant.grant_id)
        started = self.clock()
        try:
            state = self.store.load_state(plan_id, grant.grant_id)
            self._check_allowance(state, request)
            reserved = self._consume(state, request)
            self.store.save_state(plan_id, reserved)
            try:
                result, underlying_id, status, count, byte_count, snapshot_after = self._dispatch(root, grant, request)
                del result
                errors: tuple[str, ...] = ()
            except Exception as exc:
                underlying_id, status, count, byte_count, snapshot_after = None, ActionStatus.FAILED, 0, 0, None
                errors = (f"{type(exc).__name__}: {exc}",)
            if status == ActionStatus.MUTATION_APPLIED:
                reserved = reserved.model_copy(
                    update={"state": GrantState.CONSUMED_BY_MUTATION, "updated_at": self.clock()}
                )
                self.store.save_state(plan_id, reserved)
            entry = ActionJournalEntry(
                action_request_id=request.action_request_id,
                action_fingerprint=fingerprint,
                reservation_id=f"reservation-{fingerprint[:24]}",
                tool_id=request.tool_id,
                action=request.action,
                normalized_input_fingerprint=sha256_payload(request.input),
                snapshot_before=grant.repository_snapshot_fingerprint,
                snapshot_after=snapshot_after,
                underlying_record_id=underlying_id,
                status=status,
                allowance_consumed=True,
                output_count=count,
                byte_count=byte_count,
                started_at=started,
                completed_at=self.clock(),
                errors=errors,
            )
            self.store.save_action(plan_id, session_id, entry)
            new_session_state = (
                SessionState.MUTATION_APPLIED
                if status == ActionStatus.MUTATION_APPLIED
                else (SessionState.EXHAUSTED if reserved.remaining_total_actions == 0 else SessionState.ACTIVE)
            )
            updated = session.model_copy(
                update={
                    "state": new_session_state,
                    "action_count": session.action_count + 1,
                    "current_snapshot_fingerprint": snapshot_after or session.current_snapshot_fingerprint,
                    "updated_at": self.clock(),
                }
            )
            self.store.save_session(updated)
            return entry
        finally:
            lock.unlink(missing_ok=True)

    def revoke(self, plan_id: str, grant_id: str, actor: str, reason: str) -> GrantRevocation:
        grant = self.store.load_grant(plan_id, grant_id)
        state = self.store.load_state(plan_id, grant_id)
        existing = self.store._base(plan_id) / "execution-grant-revocations"
        for path in sorted(existing.glob("*.json")) if existing.exists() else ():
            value = self.store._load(path, GrantRevocation)
            if value.grant_id == grant_id:
                return value
        fp = sha256_payload({"grant": grant.grant_fingerprint, "actor": actor, "reason": reason})
        value = GrantRevocation(
            revocation_id=f"execution-revocation-{fp[:24]}",
            revocation_fingerprint=fp,
            grant_id=grant_id,
            grant_fingerprint=grant.grant_fingerprint,
            actor=actor,
            reason=reason,
            revoked_at=self.clock(),
        )
        self.store.save_revocation(plan_id, value)
        self.store.save_state(
            plan_id,
            state.model_copy(
                update={"state": GrantState.REVOKED, "version": state.version + 1, "updated_at": self.clock()}
            ),
        )
        for session in self.store.list_sessions(plan_id):
            if session.grant_id == grant_id and session.state == SessionState.ACTIVE:
                self.store.save_session(
                    session.model_copy(update={"state": SessionState.REVOKED, "updated_at": self.clock()})
                )
        return value

    def close_session(self, plan_id: str, session_id: str) -> ExecutionSession:
        session = self.store.load_session(plan_id, session_id)
        if session.state != SessionState.ACTIVE:
            return session
        value = session.model_copy(
            update={"state": SessionState.COMPLETED, "updated_at": self.clock(), "completed_at": self.clock()}
        )
        self.store.save_session(value)
        return value

    def _current(
        self, root: Path, plan_id: str, task_id: str, revision: int | None
    ) -> tuple[PlanRevision, Task, Any, str]:
        plan = self.plan_store.load_current(plan_id)
        if revision is not None and revision != plan.current_revision:
            raise AuthorizationDenied("Requested plan revision is not current")
        task = next((x for x in plan.tasks if x.task_id == task_id), None)
        if task is None:
            raise AuthorizationDenied("Task does not belong to plan")
        if task.status in {
            TaskStatus.DRAFT,
            TaskStatus.BLOCKED,
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
        }:
            raise AuthorizationDenied(f"Task status {task.status.value} is ineligible for execution authorization")
        analysis = self.analysis_store.load_current(plan_id, plan.current_revision, task_id)
        if analysis.task_fingerprint != task.task_fingerprint or analysis.graph_fingerprint != plan.graph_fingerprint:
            raise AuthorizationDenied("Task-context analysis is stale")
        if analysis.decision not in _PASSING_CONTEXT:
            raise AuthorizationDenied("Task-context fit is not passing")
        snapshot = self.inventory_service.build(root.resolve(strict=True)).snapshot.semantic_fingerprint
        if snapshot != analysis.repository_snapshot_fingerprint:
            raise AuthorizationDenied("Repository snapshot is stale")
        return plan, task, analysis, snapshot

    def _tool(self, name: str, plan: PlanRevision, task: Task, analysis: Any) -> ToolAuthorization:
        if name not in _ALLOWED_TOOLS:
            raise AuthorizationDenied(f"Tool {name} is not allowed by G7")
        definition = self.registry.get_by_name_version(name, "1.0.0")
        if any(capability not in task.requested_capabilities for capability in definition.required_capabilities):
            raise AuthorizationDenied(f"Task did not request every capability required by {name}")
        decision = ToolPolicy().evaluate(
            plan,
            task,
            definition,
            registry_fingerprint=self.registry.fingerprint,
            analysis=analysis,
            authorization_workflow=name in (_MUTATION_TOOLS | _VALIDATION_TOOLS),
        )
        if not decision.structurally_eligible or decision.decision not in {
            PolicyDecision.ELIGIBLE_FOR_FUTURE_REQUEST,
            PolicyDecision.ELIGIBLE_WITH_HUMAN_APPROVAL,
        }:
            raise AuthorizationDenied(f"G1 structural policy denied {name}: {decision.decision.value}")
        return ToolAuthorization(
            tool_id=definition.tool_id,
            tool_version=definition.tool_version,
            definition_fingerprint=definition.definition_fingerprint,
            canonical_name=definition.canonical_name,
        )

    @staticmethod
    def _validate_actions(task: Task, spec: AuthorizationSpecification, tools: tuple[ToolAuthorization, ...]) -> None:
        names = {x.canonical_name for x in tools}
        for action in spec.allowed_actions:
            if action in _READ_TOOLS and (_READ_TOOLS[action] not in names or task.status not in _READ_STATUSES):
                raise AuthorizationDenied("Read action is not eligible for this task status/tool set")
            if action == ActionKind.APPLY_MUTATION_PROPOSAL and (
                not names & _MUTATION_TOOLS or task.status not in _MUTATION_STATUSES
            ):
                raise AuthorizationDenied("Mutation action is not eligible")
            if action == ActionKind.RUN_VALIDATION_PROPOSAL and (
                not names & _VALIDATION_TOOLS or task.status not in _VALIDATION_STATUSES
            ):
                raise AuthorizationDenied("Validation action is not eligible")
        if spec.maximum_mutation_actions and not spec.permitted_mutation_proposals:
            raise AuthorizationDenied("Mutation allowance requires exact mutation proposal IDs")
        if spec.maximum_validation_actions and not spec.permitted_validation_proposals:
            raise AuthorizationDenied("Validation allowance requires exact validation proposal IDs")

    @staticmethod
    def _validate_scopes(task: Task, spec: AuthorizationSpecification) -> None:
        allowed = tuple(x.replace("\\", "/").removesuffix("/**").rstrip("/") for x in task.affected_scopes)
        forbidden = tuple(x.replace("\\", "/").removesuffix("/**").rstrip("/") for x in task.forbidden_scopes)
        for scope in (*spec.read_scopes, *spec.write_scopes):
            normalized = scope.replace("\\", "/").rstrip("/")
            if ".." in normalized.split("/") or not any(
                normalized == item or normalized.startswith(item + "/") for item in allowed
            ):
                raise AuthorizationDenied(f"Scope {scope} is broader than task affected scopes")
            if any(normalized == item or normalized.startswith(item + "/") for item in forbidden):
                raise AuthorizationDenied(f"Scope {scope} overlaps a forbidden task scope")

    def _verify_referenced_approvals(self, proposal: AuthorizationProposal) -> None:
        if proposal.permitted_mutation_proposals:
            if self.mutation_service is None:
                raise GrantRejected("G3 service is unavailable")
            for item in proposal.permitted_mutation_proposals:
                value = self.mutation_service.store.load_proposal(proposal.plan_id, item)
                approvals = self.mutation_service.store.list_approvals(proposal.plan_id, proposal_id=item)
                if (
                    value.task_id != proposal.task_id
                    or len(approvals) != 1
                    or approvals[0].decision != MutationDecision.APPROVED
                ):
                    raise GrantRejected("Exact approved G3 proposal is required")
        if proposal.permitted_validation_proposals:
            if self.validation_service is None:
                raise GrantRejected("G4 service is unavailable")
            for item in proposal.permitted_validation_proposals:
                validation_proposal = self.validation_service.store.load_proposal(proposal.plan_id, item)
                validation_approvals = self.validation_service.store.list_approvals(proposal.plan_id, item)
                if (
                    validation_proposal.task_id != proposal.task_id
                    or len(validation_approvals) != 1
                    or validation_approvals[0].decision != ValidationDecision.APPROVED
                ):
                    raise GrantRejected("Exact approved G4 proposal is required")

    def _admit(
        self, root: Path, session: ExecutionSession, grant: ExecutionGrant, request: ActionRequest, fingerprint: str
    ) -> None:
        del fingerprint
        if session.state != SessionState.ACTIVE or request.caller_type != request.caller_type.HUMAN_CLI:
            raise ActionRejected("Only human_cli may dispatch through an active session")
        if request.grant_id != grant.grant_id or request.grant_fingerprint != grant.grant_fingerprint:
            raise ActionRejected("Action grant identity does not match")
        if (request.plan_id, request.plan_revision, request.task_id, request.task_fingerprint) != (
            grant.plan_id,
            grant.plan_revision,
            grant.task_id,
            grant.task_fingerprint,
        ):
            raise ActionRejected("Action plan/task identity does not match grant")
        if request.action not in grant.allowed_actions or not any(
            x.tool_id == request.tool_id and x.tool_version == request.tool_version for x in grant.tools
        ):
            raise ActionRejected("Action or tool is not granted")
        _, _, _, snapshot = self._current(root, grant.plan_id, grant.task_id, grant.plan_revision)
        if snapshot != grant.repository_snapshot_fingerprint:
            raise ActionRejected("Grant is stale for current repository snapshot")
        if request.action in _READ_TOOLS:
            path = str(request.input.get("path") or request.input.get("directory_prefix") or "")
            if path and not any(
                path == scope or path.startswith(scope.rstrip("/") + "/") for scope in grant.read_scopes
            ):
                raise ActionRejected("Action path is outside grant read scopes")
        if (
            request.action == ActionKind.APPLY_MUTATION_PROPOSAL
            and request.input.get("proposal_id") not in grant.permitted_mutation_proposals
        ):
            raise ActionRejected("Mutation proposal is not granted")
        if (
            request.action == ActionKind.RUN_VALIDATION_PROPOSAL
            and request.input.get("proposal_id") not in grant.permitted_validation_proposals
        ):
            raise ActionRejected("Validation proposal is not granted")

    @staticmethod
    def _check_allowance(state: GrantStateRecord, request: ActionRequest) -> None:
        if state.state != GrantState.ACTIVE:
            raise AllowanceExceeded(f"Grant state is {state.state.value}")
        if state.remaining_total_actions < 1 or state.remaining_actions_per_tool.get(request.tool_id, 0) < 1:
            raise AllowanceExceeded("Invocation allowance exhausted")
        if (
            request.requested_read_bytes > state.remaining_read_bytes
            or request.requested_search_bytes > state.remaining_search_bytes
            or request.requested_output_bytes > state.remaining_output_bytes
        ):
            raise AllowanceExceeded("Byte allowance exceeded")
        if request.action == ActionKind.APPLY_MUTATION_PROPOSAL and state.remaining_mutation_actions < 1:
            raise AllowanceExceeded("Mutation allowance exhausted")
        if request.action == ActionKind.RUN_VALIDATION_PROPOSAL and state.remaining_validation_actions < 1:
            raise AllowanceExceeded("Validation allowance exhausted")

    def _consume(self, state: GrantStateRecord, request: ActionRequest) -> GrantStateRecord:
        per_tool = dict(state.remaining_actions_per_tool)
        per_tool[request.tool_id] -= 1
        total = state.remaining_total_actions - 1
        return state.model_copy(
            update={
                "state": GrantState.EXHAUSTED if total == 0 else GrantState.ACTIVE,
                "remaining_total_actions": total,
                "remaining_actions_per_tool": per_tool,
                "remaining_read_bytes": state.remaining_read_bytes - request.requested_read_bytes,
                "remaining_search_bytes": state.remaining_search_bytes - request.requested_search_bytes,
                "remaining_output_bytes": state.remaining_output_bytes - request.requested_output_bytes,
                "remaining_mutation_actions": state.remaining_mutation_actions
                - (request.action == ActionKind.APPLY_MUTATION_PROPOSAL),
                "remaining_validation_actions": state.remaining_validation_actions
                - (request.action == ActionKind.RUN_VALIDATION_PROPOSAL),
                "version": state.version + 1,
                "updated_at": self.clock(),
            }
        )

    def _dispatch(
        self, root: Path, grant: ExecutionGrant, request: ActionRequest
    ) -> tuple[object, str | None, ActionStatus, int, int, str | None]:
        if request.action in _READ_TOOLS:
            if self.read_service is None:
                raise ActionRejected("G2 read service is unavailable")
            operation = {
                ActionKind.REPO_FILES: OperationKind.LIST_FILES,
                ActionKind.REPO_READ: OperationKind.READ_FILE,
                ActionKind.REPO_READ_RANGE: OperationKind.READ_RANGE,
                ActionKind.REPO_PATH_SEARCH: OperationKind.SEARCH_PATHS,
                ActionKind.REPO_LITERAL_SEARCH: OperationKind.SEARCH_LITERAL,
            }[request.action]
            inputs = InvocationInput.model_validate({"operation": operation, **request.input})
            envelope = self.read_service.execute_human(
                root, _READ_TOOLS[request.action], inputs, correlation_id=request.correlation_id
            )
            result = envelope.result
            count = len(result.files) + len(result.matches) + int(result.content is not None)
            return (
                envelope,
                envelope.record.execution_id,
                ActionStatus.READ_COMPLETED,
                count,
                result.byte_count,
                result.repository_snapshot_fingerprint,
            )
        proposal_id = str(request.input["proposal_id"])
        if request.action == ActionKind.APPLY_MUTATION_PROPOSAL:
            if self.mutation_service is None:
                raise ActionRejected("G3 service is unavailable")
            mutation_result = self.mutation_service.apply(root, grant.plan_id, proposal_id)
            return (
                mutation_result,
                mutation_result.mutation_id,
                ActionStatus.MUTATION_APPLIED,
                mutation_result.operation_count,
                mutation_result.write_bytes,
                mutation_result.repository_snapshot_after,
            )
        if self.validation_service is None:
            raise ActionRejected("G4 service is unavailable")
        validation_result = self.validation_service.run(root, grant.plan_id, proposal_id)
        return (
            validation_result,
            validation_result.execution_id,
            ActionStatus.VALIDATION_COMPLETED,
            1,
            validation_result.stdout.byte_count + validation_result.stderr.byte_count,
            validation_result.repository_snapshot_after,
        )

    @staticmethod
    def _action_fingerprint(request: ActionRequest) -> str:
        payload = request.model_dump(mode="json", exclude={"semantic_fingerprint", "created_at"})
        fingerprint = sha256_payload(payload)
        if request.semantic_fingerprint is not None and request.semantic_fingerprint != fingerprint:
            raise ActionRejected("Action semantic fingerprint does not match")
        return fingerprint
