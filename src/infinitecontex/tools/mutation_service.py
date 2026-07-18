"""Task-bound G3 proposal, approval, and rollback-protected apply orchestration."""

from __future__ import annotations

import difflib
import hashlib
import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from infinitecontex.events.logger import EventLogger
from infinitecontex.planning.models import Capability, PlanRevision, Task, TaskStatus
from infinitecontex.planning.store import PlanStore
from infinitecontex.task_context.models import (
    PathReference,
    PathReferenceKind,
    RepositoryInventory,
    TaskContextAnalysis,
    TaskContextDecision,
)
from infinitecontex.task_context.paths import PathResolver, repository_glob_match
from infinitecontex.task_context.repository import RepositoryInventoryService
from infinitecontex.task_context.store import TaskContextAnalysisStore
from infinitecontex.tools.execution_service import NoCommandGitStateProvider
from infinitecontex.tools.models import PolicyDecision
from infinitecontex.tools.mutation_errors import MutationAdmissionError, MutationApplyError, MutationRollbackError
from infinitecontex.tools.mutation_fingerprints import (
    approval_fingerprint,
    authorization_fingerprint,
    mutation_record_fingerprint,
    proposal_fingerprint,
)
from infinitecontex.tools.mutation_models import (
    CreateTextFile,
    FileKind,
    FrozenTarget,
    MutationApproval,
    MutationAuthorization,
    MutationDecision,
    MutationExecutionRecord,
    MutationProposal,
    MutationRequest,
    MutationStatus,
    MutationValidation,
    StructuredMutation,
)
from infinitecontex.tools.mutation_store import MutationStore
from infinitecontex.tools.policy import ToolPolicy
from infinitecontex.tools.registry import ToolRegistry
from infinitecontex.tools.sensitive import SensitivePathPolicy

MAX_TARGETS = 16
MAX_OPERATIONS = 64
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_DIFF_BYTES = 128 * 1024
_PASSING = {
    TaskContextDecision.FITS_TARGET,
    TaskContextDecision.FITS_WITH_WARNING,
    TaskContextDecision.FITS_HARD_LIMIT,
}
_ELIGIBLE_TASKS = {TaskStatus.READY, TaskStatus.IN_PROGRESS, TaskStatus.AWAITING_REVIEW}
_CAPABILITY = {
    FileKind.SOURCE: Capability.WRITE_SOURCE_FILES,
    FileKind.TEST: Capability.WRITE_TESTS,
    FileKind.DOCUMENTATION: Capability.WRITE_DOCUMENTATION,
}
_TOOL = {
    FileKind.SOURCE: "repository.apply-source-patch",
    FileKind.TEST: "repository.write-test",
    FileKind.DOCUMENTATION: "repository.write-documentation",
}


@dataclass(frozen=True)
class MutationHandlerBinding:
    tool_id: str
    tool_version: str
    definition_fingerprint: str
    handler_name: str


class RepositoryMutationService:
    def __init__(
        self,
        registry: ToolRegistry,
        plan_store: PlanStore,
        analysis_store: TaskContextAnalysisStore,
        store: MutationStore,
        *,
        inventory_service: RepositoryInventoryService | None = None,
        policy: ToolPolicy | None = None,
        sensitive_policy: SensitivePathPolicy | None = None,
        event_logger: EventLogger | None = None,
        clock: Callable[[], datetime] | None = None,
        after_target_applied: Callable[[str, int], None] | None = None,
    ) -> None:
        self.registry = registry
        self.plan_store = plan_store
        self.analysis_store = analysis_store
        self.store = store
        self.sensitive_policy = sensitive_policy or SensitivePathPolicy()
        self.inventory_service = inventory_service or RepositoryInventoryService(
            git_provider=NoCommandGitStateProvider(), additional_path_filter=self.sensitive_policy.permits
        )
        self.policy = policy or ToolPolicy()
        self.event_logger = event_logger
        self.clock = clock or (lambda: datetime.now(UTC))
        self.after_target_applied = after_target_applied or (lambda _path, _count: None)
        self.mutation_bindings = {
            definition.tool_id: MutationHandlerBinding(
                definition.tool_id,
                definition.tool_version,
                definition.definition_fingerprint,
                "structured_text_transaction_v1",
            )
            for name in _TOOL.values()
            for definition in (registry.get_by_name_version(name, "1.0.0"),)
        }

    def validate_request(self, root: Path, request: MutationRequest) -> MutationValidation:
        root = root.resolve(strict=True)
        inventory = self.inventory_service.build(root)
        try:
            operations = self._normalize_operations(root, inventory, request.operations)
            targets, _previews = self._freeze(root, inventory, operations)
            total = sum(item.postimage_bytes for item in targets)
            return MutationValidation(
                valid=True,
                operation_count=len(operations),
                target_paths=tuple(item.path for item in operations),
                total_write_bytes=total,
            )
        except MutationAdmissionError as exc:
            return MutationValidation(
                valid=False,
                operation_count=len(request.operations),
                target_paths=(),
                total_write_bytes=0,
                errors=(str(exc),),
            )

    def propose(
        self,
        root: Path,
        plan_id: str,
        task_id: str,
        request: MutationRequest,
        *,
        revision: int | None = None,
    ) -> MutationProposal:
        self._event("mutation_proposal_validation_started", {"plan_id": plan_id, "task_id": task_id})
        plan = self.plan_store.load_current(plan_id)
        if revision is not None and revision != plan.current_revision:
            raise MutationAdmissionError("Requested plan revision is not current")
        task, analysis = self._task_analysis(plan, task_id)
        root = root.resolve(strict=True)
        inventory = self.inventory_service.build(root)
        if analysis.repository_snapshot_fingerprint != inventory.snapshot.semantic_fingerprint:
            raise MutationAdmissionError("Task-context analysis is stale for the repository")
        operations = self._normalize_operations(root, inventory, request.operations)
        kinds = {item.file_kind for item in operations}
        if len(kinds) != 1:
            raise MutationAdmissionError("One proposal must use one file kind and exact mutation tool")
        kind = next(iter(kinds))
        capability = _CAPABILITY[kind]
        if capability not in task.requested_capabilities:
            raise MutationAdmissionError(f"Task did not request required capability {capability.value}")
        self._validate_scopes(task, operations)
        definition = self.registry.get_by_name_version(_TOOL[kind], "1.0.0")
        binding = self.mutation_bindings.get(definition.tool_id)
        if (
            binding is None
            or binding.tool_version != definition.tool_version
            or binding.definition_fingerprint != definition.definition_fingerprint
        ):
            raise MutationAdmissionError("Exact mutation handler binding is missing or stale")
        decision = self.policy.evaluate(
            plan,
            task,
            definition,
            registry_fingerprint=self.registry.fingerprint,
            analysis=analysis,
            authorization_workflow=True,
        )
        if not decision.structurally_eligible or decision.decision != PolicyDecision.ELIGIBLE_WITH_HUMAN_APPROVAL:
            raise MutationAdmissionError(f"Tool policy denied mutation proposal: {decision.decision.value}")
        targets, previews = self._freeze(root, inventory, operations)
        total_read = sum(item.preimage_bytes for item in targets)
        total_write = sum(item.postimage_bytes for item in targets)
        if total_read > MAX_TOTAL_BYTES or total_write > MAX_TOTAL_BYTES:
            raise MutationAdmissionError("Mutation exceeds total preimage or postimage byte limit")
        diff = "".join(previews)
        abbreviated = len(diff.encode("utf-8")) > MAX_DIFF_BYTES
        if abbreviated:
            diff = diff.encode("utf-8")[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
        provisional = MutationProposal(
            proposal_id="mutation-proposal-" + "0" * 24,
            semantic_fingerprint="0" * 64,
            plan_id=plan.plan_id,
            plan_revision=plan.current_revision,
            graph_fingerprint=plan.graph_fingerprint,
            task_id=task.task_id,
            task_fingerprint=task.task_fingerprint,
            repository_snapshot_fingerprint=inventory.snapshot.semantic_fingerprint,
            analysis_id=analysis.analysis_id,
            analysis_fingerprint=analysis.semantic_fingerprint,
            tool_id=definition.tool_id,
            tool_version=definition.tool_version,
            tool_fingerprint=definition.definition_fingerprint,
            policy_decision_id=decision.decision_id,
            policy_decision_fingerprint=decision.semantic_fingerprint,
            requested_capabilities=tuple(item.value for item in task.requested_capabilities),
            affected_scopes=task.affected_scopes,
            forbidden_scopes=task.forbidden_scopes,
            operations=operations,
            targets=targets,
            operation_count=len(operations),
            total_read_bytes=total_read,
            total_write_bytes=total_write,
            diff_preview=diff,
            diff_abbreviated=abbreviated,
            warnings=("diff_preview_abbreviated",) if abbreviated else (),
            validation_passed=True,
            created_at=self.clock(),
        )
        fingerprint = proposal_fingerprint(provisional)
        proposal = provisional.model_copy(
            update={"proposal_id": f"mutation-proposal-{fingerprint[:24]}", "semantic_fingerprint": fingerprint}
        )
        self.store.save_proposal(proposal)
        self._event("mutation_proposal_persisted", {"proposal_id": proposal.proposal_id})
        return proposal

    def decide(
        self,
        plan_id: str,
        proposal_id: str,
        actor: str,
        decision: MutationDecision,
        *,
        reason: str | None = None,
        warnings_acknowledged: bool = False,
    ) -> MutationApproval:
        proposal = self.store.load_proposal(plan_id, proposal_id)
        if proposal.warnings and not warnings_acknowledged:
            raise MutationAdmissionError("Proposal warnings require explicit acknowledgement")
        provisional = MutationApproval(
            approval_id="mutation-approval-" + "0" * 24,
            approval_fingerprint="0" * 64,
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.semantic_fingerprint,
            plan_id=proposal.plan_id,
            plan_revision=proposal.plan_revision,
            repository_snapshot_fingerprint=proposal.repository_snapshot_fingerprint,
            actor=actor,
            decision=decision,
            reason=reason,
            warnings_acknowledged=warnings_acknowledged,
            decided_at=self.clock(),
        )
        fingerprint = approval_fingerprint(provisional)
        value = provisional.model_copy(
            update={"approval_id": f"mutation-approval-{fingerprint[:24]}", "approval_fingerprint": fingerprint}
        )
        self.store.save_approval(value)
        self._event("mutation_approval_recorded", {"proposal_id": proposal_id, "decision": decision.value})
        return value

    def apply(self, root: Path, plan_id: str, proposal_id: str) -> MutationExecutionRecord:
        started = self.clock()
        self._event("mutation_apply_admission_started", {"plan_id": plan_id, "proposal_id": proposal_id})
        proposal = self.store.load_proposal(plan_id, proposal_id)
        approvals = self.store.list_approvals(plan_id, proposal_id=proposal_id)
        if len(approvals) != 1 or approvals[0].decision != MutationDecision.APPROVED:
            raise MutationAdmissionError("An exact immutable human approval is required before apply")
        approval = approvals[0]
        authorization = self._authorization(proposal, approval)
        if authorization.authorization_fingerprint != authorization_fingerprint(authorization):
            raise MutationAdmissionError("Mutation authorization fingerprint is invalid")
        plan = self.plan_store.load_current(plan_id)
        if plan.current_revision != proposal.plan_revision or plan.graph_fingerprint != proposal.graph_fingerprint:
            raise MutationAdmissionError("Plan changed after mutation proposal")
        task, analysis = self._task_analysis(plan, proposal.task_id)
        if (
            task.task_fingerprint != proposal.task_fingerprint
            or analysis.semantic_fingerprint != proposal.analysis_fingerprint
        ):
            raise MutationAdmissionError("Task or task-context analysis changed after proposal")
        definition = self.registry.get(proposal.tool_id)
        if definition.definition_fingerprint != proposal.tool_fingerprint:
            raise MutationAdmissionError("Mutation tool definition changed after proposal")
        policy = self.policy.evaluate(
            plan,
            task,
            definition,
            registry_fingerprint=self.registry.fingerprint,
            analysis=analysis,
            plan_approved=True,
            authorization_workflow=True,
        )
        if not policy.structurally_eligible:
            raise MutationAdmissionError("Mutation policy is no longer eligible")
        root = root.resolve(strict=True)
        inventory = self.inventory_service.build(root)
        if inventory.snapshot.semantic_fingerprint != proposal.repository_snapshot_fingerprint:
            raise MutationAdmissionError("Repository snapshot changed after mutation proposal")
        self._validate_scopes(task, proposal.operations)
        postimages, preimages = self._materialize(root, inventory, proposal)
        temporaries = self._prepare_temporaries(root, proposal, postimages)
        completed: list[str] = []
        status = MutationStatus.APPLIED
        rollback_complete: bool | None = None
        errors: tuple[str, ...] = ()
        try:
            self._event("mutation_preconditions_verified", {"proposal_id": proposal_id})
            for index, target in enumerate(proposal.targets, 1):
                self._event("mutation_target_apply_started", {"path": target.path})
                self._verify_immediate(root, target)
                self._commit_target(root / target.path, temporaries[target.path], target.existed)
                completed.append(target.path)
                self._event("mutation_target_apply_completed", {"path": target.path})
                self.after_target_applied(target.path, index)
            for target in proposal.targets:
                if self._hash_path(root / target.path) != target.postimage_sha256:
                    raise MutationApplyError(f"Postimage verification failed for {target.path}")
        except Exception as exc:
            errors = (getattr(exc, "code", "mutation_apply_failed"),)
            try:
                self._event("mutation_rollback_started", {"proposal_id": proposal_id})
                self._rollback(root, proposal, preimages, completed)
                self._event("mutation_rollback_completed", {"proposal_id": proposal_id})
                status = MutationStatus.APPLY_FAILED_ROLLED_BACK
                rollback_complete = True
            except Exception as rollback_exc:
                status = MutationStatus.APPLY_FAILED_ROLLBACK_INCOMPLETE
                rollback_complete = False
                errors += (getattr(rollback_exc, "code", "mutation_rollback_incomplete"),)
        finally:
            for temporary in temporaries.values():
                temporary.unlink(missing_ok=True)
        after = self.inventory_service.build(root).snapshot.semantic_fingerprint
        record = self._record(proposal, approval, status, rollback_complete, errors, started, self.clock(), after)
        self.store.save_record(record)
        self._event("mutation_record_persisted", {"mutation_id": record.mutation_id, "status": status.value})
        return record

    @staticmethod
    def _authorization(proposal: MutationProposal, approval: MutationApproval) -> MutationAuthorization:
        provisional = MutationAuthorization(
            authorization_id="mutation-authorization-" + "0" * 24,
            authorization_fingerprint="0" * 64,
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.semantic_fingerprint,
            approval_id=approval.approval_id,
            approval_fingerprint=approval.approval_fingerprint,
            plan_id=proposal.plan_id,
            plan_revision=proposal.plan_revision,
            task_id=proposal.task_id,
            task_fingerprint=proposal.task_fingerprint,
            tool_id=proposal.tool_id,
            tool_fingerprint=proposal.tool_fingerprint,
            repository_snapshot_fingerprint=proposal.repository_snapshot_fingerprint,
            approved_paths=tuple(item.path for item in proposal.targets),
            preimage_hashes=tuple(item.preimage_sha256 or "new-file" for item in proposal.targets),
            postimage_hashes=tuple(item.postimage_sha256 for item in proposal.targets),
            operation_count=proposal.operation_count,
        )
        fingerprint = authorization_fingerprint(provisional)
        return provisional.model_copy(
            update={
                "authorization_id": f"mutation-authorization-{fingerprint[:24]}",
                "authorization_fingerprint": fingerprint,
            }
        )

    def _task_analysis(self, plan: PlanRevision, task_id: str) -> tuple[Task, TaskContextAnalysis]:
        task = next((item for item in plan.tasks if item.task_id == task_id), None)
        if task is None:
            raise MutationAdmissionError("Task does not belong to exact plan revision")
        if task.status not in _ELIGIBLE_TASKS:
            raise MutationAdmissionError(f"Task status {task.status.value} is not mutation-eligible")
        analysis = self.analysis_store.load_current(plan.plan_id, plan.current_revision, task.task_id)
        if (
            analysis.task_fingerprint != task.task_fingerprint
            or analysis.graph_fingerprint != plan.graph_fingerprint
            or analysis.decision not in _PASSING
        ):
            raise MutationAdmissionError("Task-context analysis is stale or not passing")
        return task, analysis

    def _normalize_operations(
        self, root: Path, inventory: RepositoryInventory, operations: tuple[StructuredMutation, ...]
    ) -> tuple[StructuredMutation, ...]:
        if len(operations) > MAX_OPERATIONS:
            raise MutationAdmissionError("Mutation exceeds the 64-operation limit")
        resolver = PathResolver(root, inventory)
        normalized: list[StructuredMutation] = []
        for item in operations:
            path, error = resolver.normalize(item.path, PathReferenceKind.EXACT_FILE)
            if error or path is None:
                raise MutationAdmissionError(error or "Invalid mutation path")
            self._validate_path(root, path)
            if item.file_kind != _classify_path(path):
                raise MutationAdmissionError(
                    f"Declared file kind does not match deterministic path classification: {path}"
                )
            normalized.append(item.model_copy(update={"path": path}))
        normalized.sort(key=lambda item: (item.path.casefold(), item.path))
        paths = [item.path for item in normalized]
        if len(set(paths)) != len(paths):
            raise MutationAdmissionError("G3 permits one structured operation per target path")
        if len(paths) > MAX_TARGETS:
            raise MutationAdmissionError("Mutation exceeds the 16-target limit")
        return tuple(normalized)

    def _validate_path(self, root: Path, path: str) -> None:
        if self.sensitive_policy.reason(path) or self.inventory_service.is_policy_excluded(path):
            raise MutationAdmissionError(f"Protected, ignored, or sensitive mutation path: {path}")
        target = root / path
        if not target.parent.exists() or not target.parent.is_dir():
            raise MutationAdmissionError("Mutation target parent directory must already exist")
        resolved = target.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise MutationAdmissionError("Mutation path escapes repository") from exc
        current = target.parent
        while current != root:
            if current.exists() and current.is_symlink():
                raise MutationAdmissionError("Mutation path crosses an unsafe link")
            current = current.parent

    @staticmethod
    def _validate_scopes(task: Task, operations: tuple[StructuredMutation, ...]) -> None:
        for operation in operations:
            if not any(_scope_match(operation.path, value) for value in task.affected_scopes):
                raise MutationAdmissionError(f"Target {operation.path} is outside affected scopes")
            if any(_scope_match(operation.path, value) for value in task.forbidden_scopes):
                raise MutationAdmissionError(f"Target {operation.path} overlaps a forbidden scope")

    def _freeze(
        self, root: Path, inventory: RepositoryInventory, operations: tuple[StructuredMutation, ...]
    ) -> tuple[tuple[FrozenTarget, ...], tuple[str, ...]]:
        targets: list[FrozenTarget] = []
        diffs: list[str] = []
        resolver = PathResolver(root, inventory)
        for operation in operations:
            old_bytes, new_bytes = self._postimage(resolver, operation)
            if len(old_bytes or b"") > MAX_FILE_BYTES or len(new_bytes) > MAX_FILE_BYTES:
                raise MutationAdmissionError(f"Target {operation.path} exceeds the 2 MiB file limit")
            old_hash = hashlib.sha256(old_bytes).hexdigest() if old_bytes is not None else None
            new_hash = hashlib.sha256(new_bytes).hexdigest()
            targets.append(
                FrozenTarget(
                    path=operation.path,
                    file_kind=operation.file_kind,
                    existed=old_bytes is not None,
                    preimage_sha256=old_hash,
                    postimage_sha256=new_hash,
                    preimage_bytes=len(old_bytes or b""),
                    postimage_bytes=len(new_bytes),
                )
            )
            old_text = (old_bytes or b"").decode("utf-8")
            new_text = new_bytes.decode("utf-8")
            diffs.append(
                "".join(
                    difflib.unified_diff(
                        old_text.splitlines(keepends=True),
                        new_text.splitlines(keepends=True),
                        fromfile=f"a/{operation.path}" if old_bytes is not None else "/dev/null",
                        tofile=f"b/{operation.path}",
                    )
                )
            )
        return tuple(targets), tuple(diffs)

    def _postimage(self, resolver: PathResolver, operation: StructuredMutation) -> tuple[bytes | None, bytes]:
        if isinstance(operation, CreateTextFile):
            if (
                resolver.resolve(_reference(operation.path, resolver)).sources
                or (resolver.root / operation.path).exists()
            ):
                raise MutationAdmissionError(f"New target already exists: {operation.path}")
            new = operation.content.encode("utf-8")
            if hashlib.sha256(new).hexdigest() != operation.expected_postimage_sha256:
                raise MutationAdmissionError(f"Expected postimage hash mismatch for {operation.path}")
            return None, new
        frozen = resolver.resolve(_reference(operation.path, resolver))
        if not frozen.sources:
            raise MutationAdmissionError(f"Existing UTF-8 text target is unavailable: {operation.path}")
        source = frozen.sources[0]
        old = (resolver.root / operation.path).read_bytes()
        if hashlib.sha256(old).hexdigest() != source.full_content_hash:
            raise MutationAdmissionError(f"Target changed while freezing {operation.path}")
        try:
            old.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MutationAdmissionError(f"Target is not strict UTF-8 text: {operation.path}") from exc
        if hashlib.sha256(old).hexdigest() != operation.expected_preimage_sha256:
            raise MutationAdmissionError(f"Expected preimage hash mismatch for {operation.path}")
        lines = old.decode("utf-8").splitlines(keepends=True)
        if operation.end_line > len(lines):
            raise MutationAdmissionError(f"Line range exceeds {operation.path}")
        old_range = "".join(lines[operation.start_line - 1 : operation.end_line])
        if old_range != operation.expected_old_text:
            raise MutationAdmissionError(f"Expected old text mismatch for {operation.path}")
        new_text = (
            "".join(lines[: operation.start_line - 1])
            + operation.replacement_text
            + "".join(lines[operation.end_line :])
        )
        new = new_text.encode("utf-8")
        if hashlib.sha256(new).hexdigest() != operation.expected_postimage_sha256:
            raise MutationAdmissionError(f"Expected postimage hash mismatch for {operation.path}")
        return old, new

    def _materialize(
        self, root: Path, inventory: RepositoryInventory, proposal: MutationProposal
    ) -> tuple[dict[str, bytes], dict[str, bytes | None]]:
        resolver = PathResolver(root, inventory)
        postimages: dict[str, bytes] = {}
        preimages: dict[str, bytes | None] = {}
        for operation, target in zip(proposal.operations, proposal.targets, strict=True):
            old, new = self._postimage(resolver, operation)
            if (hashlib.sha256(old).hexdigest() if old is not None else None) != target.preimage_sha256:
                raise MutationAdmissionError(f"Preimage changed for {target.path}")
            if hashlib.sha256(new).hexdigest() != target.postimage_sha256:
                raise MutationAdmissionError(f"Postimage contract changed for {target.path}")
            preimages[target.path], postimages[target.path] = old, new
        return postimages, preimages

    @staticmethod
    def _prepare_temporaries(root: Path, proposal: MutationProposal, postimages: dict[str, bytes]) -> dict[str, Path]:
        values: dict[str, Path] = {}
        try:
            for target in proposal.targets:
                path = root / target.path
                descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.g3-", suffix=".tmp", dir=path.parent)
                temporary = Path(name)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(postimages[target.path])
                    stream.flush()
                    os.fsync(stream.fileno())
                if target.existed:
                    os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
                values[target.path] = temporary
            return values
        except Exception:
            for value in values.values():
                value.unlink(missing_ok=True)
            raise

    @staticmethod
    def _commit_target(target: Path, temporary: Path, existed: bool) -> None:
        if existed:
            os.replace(temporary, target)
        else:
            os.link(temporary, target)
            temporary.unlink(missing_ok=True)

    def _rollback(
        self, root: Path, proposal: MutationProposal, preimages: dict[str, bytes | None], completed: list[str]
    ) -> None:
        for path_value in reversed(completed):
            path = root / path_value
            old = preimages[path_value]
            if old is None:
                path.unlink(missing_ok=True)
            else:
                descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.rollback-", suffix=".tmp", dir=path.parent)
                temporary = Path(name)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(old)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
        for target in proposal.targets:
            expected = target.preimage_sha256
            actual = self._hash_path(root / target.path) if (root / target.path).exists() else None
            if actual != expected:
                raise MutationRollbackError(f"Rollback verification failed for {target.path}: {actual} != {expected}")

    @staticmethod
    def _verify_immediate(root: Path, target: FrozenTarget) -> None:
        path = root / target.path
        if path.is_symlink() or (target.existed != path.exists()):
            raise MutationApplyError(f"Target identity changed for {target.path}")
        actual = RepositoryMutationService._hash_path(path) if target.existed else None
        if actual != target.preimage_sha256:
            raise MutationApplyError(f"Immediate preimage verification failed for {target.path}")

    @staticmethod
    def _hash_path(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _record(
        self,
        proposal: MutationProposal,
        approval: MutationApproval,
        status: MutationStatus,
        rollback: bool | None,
        errors: tuple[str, ...],
        started: datetime,
        completed: datetime,
        after: str,
    ) -> MutationExecutionRecord:
        provisional = MutationExecutionRecord(
            mutation_id="tool-mutation-" + "0" * 24,
            record_fingerprint="0" * 64,
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.semantic_fingerprint,
            approval_id=approval.approval_id,
            approval_fingerprint=approval.approval_fingerprint,
            plan_id=proposal.plan_id,
            plan_revision=proposal.plan_revision,
            task_id=proposal.task_id,
            task_fingerprint=proposal.task_fingerprint,
            tool_id=proposal.tool_id,
            tool_fingerprint=proposal.tool_fingerprint,
            repository_snapshot_before=proposal.repository_snapshot_fingerprint,
            repository_snapshot_after=after,
            target_paths=tuple(item.path for item in proposal.targets),
            preimage_hashes=tuple(item.preimage_sha256 or "new-file" for item in proposal.targets),
            postimage_hashes=tuple(item.postimage_sha256 for item in proposal.targets),
            operation_count=proposal.operation_count,
            read_bytes=proposal.total_read_bytes,
            write_bytes=proposal.total_write_bytes,
            status=status,
            rollback_complete=rollback,
            warning_codes=proposal.warnings,
            error_codes=errors,
            started_at=started,
            completed_at=completed,
        )
        fingerprint = mutation_record_fingerprint(provisional)
        return provisional.model_copy(
            update={"mutation_id": f"tool-mutation-{fingerprint[:24]}", "record_fingerprint": fingerprint}
        )

    @staticmethod
    def _declared_write_bytes(operation: StructuredMutation) -> int:
        value = operation.content if isinstance(operation, CreateTextFile) else operation.replacement_text
        return len(value.encode("utf-8"))

    def _event(self, event: str, payload: dict[str, object]) -> None:
        if self.event_logger is not None:
            self.event_logger.log(event, payload)


def _classify_path(path: str) -> FileKind:
    normalized = path.casefold()
    name = normalized.rsplit("/", 1)[-1]
    suffix = Path(name).suffix
    if normalized.startswith(("tests/", "test/")) or name.startswith("test_") or name.endswith("_test.py"):
        return FileKind.TEST
    if normalized.startswith(("docs/", "documentation/")) or suffix in {".md", ".rst", ".adoc"}:
        return FileKind.DOCUMENTATION
    if suffix in {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg"}:
        raise MutationAdmissionError(f"Configuration mutation is unsupported in G3: {path}")
    if normalized.startswith(("src/", "source/", "lib/", "app/")) or suffix in {
        ".py",
        ".cs",
        ".js",
        ".ts",
        ".tsx",
        ".java",
        ".cpp",
        ".c",
        ".h",
    }:
        return FileKind.SOURCE
    raise MutationAdmissionError(f"Ambiguous or unsupported text target classification: {path}")


def _scope_match(path: str, scope: str) -> bool:
    scope = scope.replace("\\", "/")
    return path == scope or repository_glob_match(path, scope) or path.startswith(scope.rstrip("/") + "/")


def _reference(path: str, resolver: PathResolver) -> PathReference:
    return PathReference(
        original_value=path,
        kind=PathReferenceKind.EXACT_FILE,
        source_task_id=path,
        source_field="g3-mutation",
        expected_snapshot_fingerprint=resolver.inventory.snapshot.semantic_fingerprint,
    )
