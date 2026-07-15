"""Immutable compact persistence for offline tool-policy decisions."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import orjson
from pydantic import ValidationError

from infinitecontex.tools.errors import MalformedToolDecisionError, ToolDecisionPersistenceError
from infinitecontex.tools.fingerprints import decision_fingerprint
from infinitecontex.tools.models import TOOL_DECISION_SCHEMA_VERSION, ToolPolicyDecision

_PLAN = re.compile(r"^plan-[0-9a-f]{24}$")
_TASK = re.compile(r"^task-[0-9a-f]{24}$")
_DECISION = re.compile(r"^tool-decision-[0-9a-f]{24}$")


class ToolDecisionStore:
    def __init__(self, plans_directory: Path) -> None:
        self.plans_directory = plans_directory

    def save(self, decision: ToolPolicyDecision) -> Path:
        self._verify(decision)
        directory = self._directory(decision.plan_id, decision.plan_revision, decision.task_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{decision.decision_id}.json"
        if target.exists():
            existing = self._load(target)
            if existing.semantic_fingerprint == decision.semantic_fingerprint:
                return target
            raise ToolDecisionPersistenceError("Immutable tool decision ID collides with different content")
        descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=directory)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(self.serialize(decision))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise ToolDecisionPersistenceError(f"Tool decision {decision.decision_id} already exists") from exc
            except OSError as exc:
                raise ToolDecisionPersistenceError(f"Could not persist immutable tool decision: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def load(self, plan_id: str, decision_id: str, *, revision: int | None = None) -> ToolPolicyDecision:
        self._validate(plan_id, decision_id=decision_id)
        root = self._root(plan_id)
        pattern = f"{revision:06d}/*/{decision_id}.json" if revision is not None else f"*/*/{decision_id}.json"
        matches = sorted(root.glob(pattern)) if root.exists() else []
        if len(matches) != 1:
            raise ToolDecisionPersistenceError(
                f"Tool decision {decision_id} was not found uniquely; run `infctx plan tool-decision list`"
            )
        return self._load(matches[0])

    def list(
        self,
        plan_id: str,
        *,
        revision: int | None = None,
        task_id: str | None = None,
    ) -> tuple[ToolPolicyDecision, ...]:
        self._validate(plan_id, task_id=task_id)
        root = self._root(plan_id)
        if not root.exists():
            return ()
        revision_pattern = f"{revision:06d}" if revision is not None else "*"
        task_pattern = task_id or "*"
        values = tuple(self._load(path) for path in sorted(root.glob(f"{revision_pattern}/{task_pattern}/*.json")))
        return tuple(
            sorted(values, key=lambda item: (item.plan_revision, item.task_id, item.tool_id, item.decision_id))
        )

    @staticmethod
    def serialize(decision: ToolPolicyDecision) -> bytes:
        return orjson.dumps(
            decision.model_dump(mode="json"),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE,
        )

    def _load(self, path: Path) -> ToolPolicyDecision:
        try:
            payload = orjson.loads(path.read_bytes())
            if not isinstance(payload, dict):
                raise ValueError("decision must be an object")
            if payload.get("schema_version") != TOOL_DECISION_SCHEMA_VERSION:
                raise MalformedToolDecisionError(
                    f"Unsupported tool decision schema {payload.get('schema_version')!r}; upgrade Infinite Context"
                )
            decision = ToolPolicyDecision.model_validate(payload)
            self._verify(decision)
            return decision
        except MalformedToolDecisionError:
            raise
        except (OSError, ValueError, orjson.JSONDecodeError, ValidationError) as exc:
            raise MalformedToolDecisionError(f"Tool decision {path.name} is malformed; move it aside") from exc

    @staticmethod
    def _verify(decision: ToolPolicyDecision) -> None:
        fingerprint = decision_fingerprint(decision)
        if fingerprint != decision.semantic_fingerprint:
            raise MalformedToolDecisionError("Tool decision semantic fingerprint does not match its content")
        if decision.decision_id != f"tool-decision-{fingerprint[:24]}":
            raise MalformedToolDecisionError("Tool decision ID is inconsistent with its fingerprint")

    def _root(self, plan_id: str) -> Path:
        self._validate(plan_id)
        return self.plans_directory / plan_id / "tool-decisions"

    def _directory(self, plan_id: str, revision: int, task_id: str) -> Path:
        self._validate(plan_id, task_id=task_id)
        if revision < 1:
            raise ToolDecisionPersistenceError("Plan revision must be positive")
        return self._root(plan_id) / f"{revision:06d}" / task_id

    @staticmethod
    def _validate(plan_id: str, *, task_id: str | None = None, decision_id: str | None = None) -> None:
        if not _PLAN.fullmatch(plan_id):
            raise ToolDecisionPersistenceError("Invalid plan ID")
        if task_id is not None and not _TASK.fullmatch(task_id):
            raise ToolDecisionPersistenceError("Invalid task ID")
        if decision_id is not None and not _DECISION.fullmatch(decision_id):
            raise ToolDecisionPersistenceError("Invalid tool decision ID")
