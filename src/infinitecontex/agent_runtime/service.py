from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol

from infinitecontex.agent_runtime.models import (
    AgentRun,
    AgentStep,
    Checkpoint,
    FinalResponse,
    MutationCandidate,
    RunState,
    RuntimeBudgets,
    ToolRequest,
)
from infinitecontex.agent_runtime.prompt import build_prompt
from infinitecontex.agent_runtime.protocol import parse_response
from infinitecontex.agent_runtime.store import AgentRunStore
from infinitecontex.llm.models import ChatMessage
from infinitecontex.model_profiles.store import ModelProfileStore
from infinitecontex.planning.store import PlanStore
from infinitecontex.task_execution.models import ActionKind, ActionRequest, CallerType, GrantState
from infinitecontex.task_execution.service import TaskExecutionService
from infinitecontex.tools.fingerprints import sha256_payload
from infinitecontex.tools.mutation_models import MutationRequest
from infinitecontex.tools.validation_definitions import ValidationCommandRegistry


class ModelAdapter(Protocol):
    def installed_identity(self, model: str) -> str: ...
    def generate(self, model: str, prompt: str) -> tuple[str, int | None, int | None]: ...


class OllamaModelAdapter:
    def __init__(self, client: object) -> None:
        self.client = client

    def installed_identity(self, model: str) -> str:
        values = self.client.list_models()  # type: ignore[attr-defined]
        match = next((x for x in values if x.name.casefold() == model.casefold()), None)
        if match is None or not match.digest:
            raise ValueError("exact configured Ollama model is not installed")
        return str(match.digest)

    def generate(self, model: str, prompt: str) -> tuple[str, int | None, int | None]:
        chunks = list(self.client.stream_chat(model, [ChatMessage(role="system", content=prompt)]))  # type: ignore[attr-defined]
        return (
            "".join(x.content for x in chunks),
            next((x.prompt_eval_count for x in reversed(chunks) if x.prompt_eval_count is not None), None),
            next((x.eval_count for x in reversed(chunks) if x.eval_count is not None), None),
        )


class SupervisedAgentService:
    def __init__(
        self,
        plans: PlanStore,
        profiles: ModelProfileStore,
        execution: TaskExecutionService,
        store: AgentRunStore,
        model: ModelAdapter,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.plans, self.profiles, self.execution, self.store, self.model = plans, profiles, execution, store, model
        self.clock = clock or (lambda: datetime.now(UTC))

    def create(
        self,
        plan_id: str,
        task_id: str,
        grant_id: str,
        session_id: str,
        profile_id: str,
        budgets: RuntimeBudgets | None = None,
    ) -> AgentRun:
        plan = self.plans.load_current(plan_id)
        task = next((x for x in plan.tasks if x.task_id == task_id), None)
        if task is None:
            raise ValueError("task missing")
        grant = self.execution.store.load_grant(plan_id, grant_id)
        session = self.execution.store.load_session(plan_id, session_id)
        if CallerType.SUPERVISED_LOCAL_AGENT not in grant.allowed_callers:
            raise ValueError("grant does not explicitly authorize supervised_local_agent")
        if CallerType.FUTURE_AGENT in grant.allowed_callers:
            raise ValueError("future_agent remains disabled")
        if session.grant_id != grant_id or grant.task_id != task_id:
            raise ValueError("grant/session/task linkage mismatch")
        profile = self.profiles.find_by_id(profile_id)
        digest = profile.model_identity.model_digest
        if digest is None or self.model.installed_identity(profile.model_identity.model_name) != digest:
            raise ValueError("installed model digest does not match exact ModelProfile")
        semantic = {
            "plan": plan.revision_fingerprint,
            "task": task.task_fingerprint,
            "grant": grant.grant_fingerprint,
            "session": session.session_fingerprint,
            "profile": profile.model_dump(mode="json"),
            "budgets": (budgets or RuntimeBudgets()).model_dump(),
        }
        fp = sha256_payload(semantic)
        now = self.clock()
        run = AgentRun(
            run_id=f"agent-run-{fp[:24]}",
            semantic_fingerprint=fp,
            plan_id=plan_id,
            plan_revision=plan.current_revision,
            task_id=task_id,
            task_fingerprint=task.task_fingerprint,
            grant_id=grant_id,
            grant_fingerprint=grant.grant_fingerprint,
            session_id=session_id,
            model_profile_id=profile_id,
            model_profile_fingerprint=sha256_payload(profile.model_dump(mode="json")),
            model_name=profile.model_identity.model_name,
            model_digest=digest,
            repository_snapshot_fingerprint=grant.repository_snapshot_fingerprint,
            analysis_fingerprint=grant.analysis_fingerprint,
            budgets=budgets or RuntimeBudgets(),
            state=RunState.CREATED,
            created_at=now,
            updated_at=now,
        )
        self.store.save_run(run)
        return run

    def run(self, root: Path, plan_id: str, run_id: str) -> AgentRun:
        run = self.store.load_run(plan_id, run_id)
        if run.state not in {RunState.CREATED, RunState.RUNNING}:
            return run
        started = monotonic()
        history: list[dict[str, object]] = []
        plan = self.plans.load_current(plan_id)
        task = next(x for x in plan.tasks if x.task_id == run.task_id)
        grant = self.execution.store.load_grant(plan_id, run.grant_id)
        while (
            run.model_calls < run.budgets.maximum_model_calls
            and monotonic() - started < run.budgets.maximum_wall_seconds
        ):
            state = self.execution.store.load_state(plan_id, run.grant_id)
            if state.state != GrantState.ACTIVE:
                return self._state(run, RunState.GRANT_EXHAUSTED)
            prompt, prompt_fp = build_prompt(plan, task, grant, state, tuple(history))
            estimated = (len(prompt.encode("utf-8")) + 3) // 4
            profile = self.profiles.find_by_id(run.model_profile_id)
            if estimated + profile.reserved_output_tokens > profile.operational_context_tokens:
                return self._state(run, RunState.BUDGET_EXHAUSTED)
            step_no = run.model_calls + 1
            began = self.clock()
            raw, _, output_tokens = self.model.generate(run.model_name, prompt)
            errors: tuple[str, ...] = ()
            response = None
            try:
                response = parse_response(raw)
            except ValueError as exc:
                errors = (str(exc),)
            response_fp = sha256_payload(response.model_dump(mode="json")) if response else None
            step_fingerprint = sha256_payload({"run": run.run_id, "step": step_no, "raw": sha256_payload(raw)})
            step = AgentStep(
                step_id=f"agent-step-{step_fingerprint[:24]}",
                step_number=step_no,
                prompt_fingerprint=prompt_fp,
                estimated_input_tokens=estimated,
                reserved_output_tokens=profile.reserved_output_tokens,
                model_name=run.model_name,
                model_digest=run.model_digest,
                generation_fingerprint=sha256_payload(
                    {"temperature": 0.1, "seed": 7, "output": profile.reserved_output_tokens}
                ),
                raw_response_hash=sha256_payload(raw),
                response_kind=response.kind if response else None,
                response_fingerprint=response_fp,
                schema_valid=response is not None,
                errors=errors,
                started_at=began,
                completed_at=self.clock(),
            )
            self.store.save_step(plan_id, run_id, step)
            run = run.model_copy(
                update={
                    "state": RunState.RUNNING,
                    "model_calls": step_no,
                    "valid_responses": run.valid_responses + int(response is not None),
                    "invalid_responses": run.invalid_responses + int(response is None),
                    "updated_at": self.clock(),
                }
            )
            self.store.save_run(run)
            if response is None:
                if run.invalid_responses >= run.budgets.maximum_invalid_responses:
                    return self._state(run, RunState.PROTOCOL_FAILED)
                history.append({"protocol_error": errors[0]})
                continue
            if isinstance(response, MutationCandidate):
                MutationRequest.model_validate({"operations": response.operations})
                commands = ValidationCommandRegistry()
                for command_id in response.suggested_validation_commands:
                    commands.get(command_id)
                self.store.save_candidate(plan_id, run_id, response)
                return self._state(run, RunState.AWAITING_MUTATION)
            if isinstance(response, Checkpoint):
                return self._state(run, RunState.AWAITING_CHECKPOINT)
            if isinstance(response, FinalResponse):
                return self._state(run, RunState.COMPLETED)
            assert isinstance(response, ToolRequest)
            if run.dispatched_actions >= run.budgets.maximum_actions:
                return self._state(run, RunState.BUDGET_EXHAUSTED)
            tool = next(
                (
                    x
                    for x in grant.tools
                    if x.canonical_name
                    == {
                        "repo_files": "repository.list-inventory",
                        "repo_read": "repository.read-file",
                        "repo_read_range": "repository.read-source-range",
                        "repo_path_search": "repository.search-paths",
                        "repo_literal_search": "repository.search-literal",
                    }[response.action_type]
                ),
                None,
            )
            if tool is None:
                history.append({"rejected": "tool not granted"})
                continue
            action = ActionRequest(
                action_request_id=f"action-agent-{run.run_id[-8:]}-{step_no}",
                grant_id=grant.grant_id,
                grant_fingerprint=grant.grant_fingerprint,
                plan_id=plan_id,
                plan_revision=run.plan_revision,
                task_id=run.task_id,
                task_fingerprint=run.task_fingerprint,
                tool_id=tool.tool_id,
                tool_version=tool.tool_version,
                action=ActionKind(response.action_type),
                caller_type=CallerType.SUPERVISED_LOCAL_AGENT,
                input=response.arguments,
                requested_output_bytes=min(128 * 1024, run.budgets.maximum_tool_bytes - run.returned_tool_bytes),
                created_at=self.clock(),
            )
            entry, transient_result = self.execution.run_with_result(root, plan_id, run.session_id, action)
            run = run.model_copy(
                update={
                    "dispatched_actions": run.dispatched_actions + 1,
                    "returned_tool_bytes": run.returned_tool_bytes + entry.byte_count,
                    "updated_at": self.clock(),
                }
            )
            self.store.save_run(run)
            tool_result: object = {"status": entry.status.value, "bytes": entry.byte_count}
            if transient_result is not None and hasattr(transient_result, "result"):
                tool_result = transient_result.result.model_dump(mode="json")
            history.append({"action": response.action_type, "result": tool_result})
        return self._state(run, RunState.BUDGET_EXHAUSTED)

    def _state(self, run: AgentRun, state: RunState) -> AgentRun:
        value = run.model_copy(update={"state": state, "updated_at": self.clock()})
        self.store.save_run(value)
        return value
