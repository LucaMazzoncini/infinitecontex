"""Fail-closed G2 read-only execution admission and dispatch gateway."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from infinitecontex.events.logger import EventLogger
from infinitecontex.task_context.models import RepositoryInventory
from infinitecontex.tools.errors import ToolMissingError
from infinitecontex.tools.execution_errors import (
    ExecutionAdmissionError,
    HandlerFingerprintMismatchError,
    InvalidGrantError,
    MissingHandlerError,
    SensitiveRepositoryPathError,
    StaleRepositorySnapshotError,
)
from infinitecontex.tools.execution_fingerprints import grant_fingerprint, invocation_fingerprint, record_fingerprint
from infinitecontex.tools.execution_handlers import ReadHandlerContext, ReadHandlerRegistry
from infinitecontex.tools.execution_models import (
    CallerType,
    ExecutionEnvelope,
    ExecutionStatus,
    ReadOnlyExecutionGrant,
    RepositoryToolResult,
    ToolExecutionRecord,
    ToolInvocation,
)
from infinitecontex.tools.execution_store import ToolExecutionStore
from infinitecontex.tools.fingerprints import sha256_payload
from infinitecontex.tools.models import ImplementationStatus
from infinitecontex.tools.registry import ToolRegistry
from infinitecontex.tools.sensitive import SensitivePathPolicy
from infinitecontex.tools.validation import validate_tool_definition


class ReadOnlyExecutionGateway:
    def __init__(
        self,
        registry: ToolRegistry,
        handlers: ReadHandlerRegistry,
        store: ToolExecutionStore,
        *,
        event_logger: EventLogger | None = None,
        sensitive_policy: SensitivePathPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry
        self.handlers = handlers
        self.store = store
        self.event_logger = event_logger
        self.sensitive_policy = sensitive_policy or SensitivePathPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        invocation: ToolInvocation,
        grant: ReadOnlyExecutionGrant | None,
        inventory: RepositoryInventory,
        repository_root: object,
    ) -> ExecutionEnvelope:
        started = self.clock()
        self._event("tool_invocation_received", invocation, {})
        definition_fingerprint = "0" * 64
        try:
            try:
                definition = self.registry.get(invocation.tool_id)
            except ToolMissingError as exc:
                raise ExecutionAdmissionError("The invocation names an unsupported tool") from exc
            definition_fingerprint = definition.definition_fingerprint
            validate_tool_definition(definition)
            self._event("tool_definition_resolved", invocation, {})
            if invocation.invocation_fingerprint != invocation_fingerprint(invocation):
                raise ExecutionAdmissionError("Invocation fingerprint does not match the immutable request")
            if invocation.registry_fingerprint != self.registry.fingerprint:
                raise ExecutionAdmissionError("Registry fingerprint changed after invocation creation")
            if definition.tool_version != invocation.tool_version:
                raise ExecutionAdmissionError("Tool version does not match the invocation")
            if definition.implementation_status != ImplementationStatus.AVAILABLE_FOR_FUTURE_EXECUTION:
                raise ExecutionAdmissionError("Tool is not enabled by the G2 read-only execution policy")
            if not definition.effects.read_only or not definition.effects.reads_repository:
                raise ExecutionAdmissionError("G2 permits repository read-only definitions only")
            binding = self.handlers.get(definition.tool_id)
            if binding is None:
                raise MissingHandlerError("No read-only handler is registered for this exact tool")
            if (
                binding.definition_fingerprint != definition.definition_fingerprint
                or binding.tool_version != definition.tool_version
            ):
                raise HandlerFingerprintMismatchError("Handler binding does not match the exact tool definition")
            if inventory.snapshot.semantic_fingerprint != invocation.repository_snapshot_fingerprint:
                raise StaleRepositorySnapshotError("Repository snapshot changed after admission")
            self._validate_grant(invocation, grant, definition.definition_fingerprint)
            for value in (
                invocation.normalized_input.path,
                invocation.normalized_input.glob,
                invocation.normalized_input.directory_prefix,
            ):
                if value and self.sensitive_policy.reason(value):
                    raise SensitiveRepositoryPathError("Sensitive repository paths are denied in G2")
            if invocation.caller_type == CallerType.FUTURE_AGENT:
                raise ExecutionAdmissionError("Future-agent requests are disabled in G2")
            self._event("tool_grant_validated", invocation, {})
            self._event("tool_snapshot_validated", invocation, {})
            self._event("tool_handler_started", invocation, {"handler": binding.handler_name})
            from pathlib import Path

            result = binding.handler(
                invocation.normalized_input,
                ReadHandlerContext(Path(str(repository_root)), inventory),
            )
            self._event("tool_handler_completed", invocation, {"status": result.status.value})
            decision = (
                "executed_read_only"
                if result.status not in {ExecutionStatus.REJECTED, ExecutionStatus.INTERNAL_FAILURE}
                else "rejected"
            )
        except ExecutionAdmissionError as exc:
            status = ExecutionStatus.REJECTED
            if isinstance(exc, StaleRepositorySnapshotError):
                status = ExecutionStatus.STALE_SNAPSHOT
            elif isinstance(exc, SensitiveRepositoryPathError):
                status = ExecutionStatus.SENSITIVE_PATH
            result = RepositoryToolResult(
                status=status,
                operation=invocation.normalized_input.operation,
                repository_snapshot_fingerprint=invocation.repository_snapshot_fingerprint,
                error_codes=(exc.code,),
                message=str(exc),
            )
            decision = "rejected"
            self._event("tool_invocation_rejected", invocation, {"code": exc.code})
        except Exception:
            result = RepositoryToolResult(
                status=ExecutionStatus.INTERNAL_FAILURE,
                operation=invocation.normalized_input.operation,
                repository_snapshot_fingerprint=invocation.repository_snapshot_fingerprint,
                error_codes=("internal_handler_failure",),
                message="The read-only handler failed internally; inspect the execution record.",
            )
            decision = "failed"
        record = self._record(invocation, definition_fingerprint, result, decision, started, self.clock())
        self.store.save(record)
        self._event("tool_execution_record_persisted", invocation, {"execution_id": record.execution_id})
        return ExecutionEnvelope(invocation=invocation, grant=grant, result=result, record=record)

    @staticmethod
    def _validate_grant(
        invocation: ToolInvocation, grant: ReadOnlyExecutionGrant | None, tool_fingerprint: str
    ) -> None:
        if grant is None:
            raise InvalidGrantError("Every G2 execution requires an invocation-scoped read grant")
        if grant.grant_fingerprint != grant_fingerprint(grant):
            raise InvalidGrantError("Read grant fingerprint is invalid")
        if any(
            (
                grant.invocation_id != invocation.invocation_id,
                grant.invocation_fingerprint != invocation.invocation_fingerprint,
                grant.tool_id != invocation.tool_id,
                grant.tool_fingerprint != tool_fingerprint,
                grant.repository_snapshot_fingerprint != invocation.repository_snapshot_fingerprint,
                grant.authorization_source != invocation.authorization_source,
                grant.authorized_scopes != invocation.requested_scopes,
            )
        ):
            raise InvalidGrantError("Read grant does not match the exact invocation, tool, snapshot, and scopes")

    def _record(
        self,
        invocation: ToolInvocation,
        tool_fingerprint: str,
        result: RepositoryToolResult,
        decision: str,
        started: datetime,
        completed: datetime,
    ) -> ToolExecutionRecord:
        hashes = {item.content_hash for item in result.files if item.content_hash}
        hashes.update(item.content_hash for item in result.matches)
        if result.content_hash:
            hashes.add(result.content_hash)
        provisional = ToolExecutionRecord(
            execution_id="tool-execution-" + "0" * 24,
            record_fingerprint="0" * 64,
            invocation_id=invocation.invocation_id,
            invocation_fingerprint=invocation.invocation_fingerprint,
            correlation_id=invocation.correlation_id,
            tool_id=invocation.tool_id,
            tool_version=invocation.tool_version,
            tool_fingerprint=tool_fingerprint,
            registry_fingerprint=invocation.registry_fingerprint,
            repository_snapshot_fingerprint=invocation.repository_snapshot_fingerprint,
            plan_id=invocation.plan_id,
            plan_revision=invocation.plan_revision,
            task_id=invocation.task_id,
            task_fingerprint=invocation.task_fingerprint,
            authorization_source=invocation.authorization_source,
            normalized_request_hash=sha256_payload(invocation.normalized_input.model_dump(mode="json")),
            decision=decision,
            result_status=result.status,
            result_count=len(result.files) + len(result.matches) + int(result.content is not None),
            byte_count=result.byte_count,
            files_scanned=result.files_scanned,
            bytes_scanned=result.bytes_scanned,
            content_hashes=tuple(sorted(hashes)),
            warning_codes=result.warnings,
            error_codes=result.error_codes,
            content_returned=result.content is not None,
            started_at=started,
            completed_at=completed,
        )
        fingerprint = record_fingerprint(provisional)
        return provisional.model_copy(
            update={"execution_id": f"tool-execution-{fingerprint[:24]}", "record_fingerprint": fingerprint}
        )

    def _event(self, event_type: str, invocation: ToolInvocation, payload: dict[str, object]) -> None:
        if self.event_logger is not None:
            self.event_logger.log(
                event_type, {"invocation_id": invocation.invocation_id, "tool_id": invocation.tool_id, **payload}
            )
