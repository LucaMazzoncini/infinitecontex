from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TypeVar

import orjson
from pydantic import BaseModel

from infinitecontex.agent_runtime.models import AgentRun, AgentStep, MutationCandidate

T = TypeVar("T", bound=BaseModel)


class AgentRunStore:
    def __init__(self, plans: Path) -> None:
        self.plans = plans

    def _dir(self, plan_id: str, run_id: str) -> Path:
        return self.plans / plan_id / "agent-runs" / run_id

    @staticmethod
    def serialize(value: BaseModel) -> bytes:
        return orjson.dumps(
            value.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE
        )

    def _replace(self, path: Path, value: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(self.serialize(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)

    def save_run(self, value: AgentRun) -> None:
        self._replace(self._dir(value.plan_id, value.run_id) / "run.json", value)

    def load_run(self, plan_id: str, run_id: str) -> AgentRun:
        return AgentRun.model_validate(orjson.loads((self._dir(plan_id, run_id) / "run.json").read_bytes()))

    def save_step(self, plan_id: str, run_id: str, value: AgentStep) -> None:
        path = self._dir(plan_id, run_id) / "steps" / f"{value.step_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return
        self._replace(path, value)

    def list_steps(self, plan_id: str, run_id: str) -> tuple[AgentStep, ...]:
        directory = self._dir(plan_id, run_id) / "steps"
        return (
            tuple(AgentStep.model_validate(orjson.loads(x.read_bytes())) for x in sorted(directory.glob("*.json")))
            if directory.exists()
            else ()
        )

    def save_candidate(self, plan_id: str, run_id: str, value: MutationCandidate) -> None:
        self._replace(self._dir(plan_id, run_id) / "mutation-candidate.json", value)

    def load_candidate(self, plan_id: str, run_id: str) -> MutationCandidate:
        return MutationCandidate.model_validate(
            orjson.loads((self._dir(plan_id, run_id) / "mutation-candidate.json").read_bytes())
        )
