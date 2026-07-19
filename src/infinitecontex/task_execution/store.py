"""Atomic repository-local persistence for G7 records and allowance state."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TypeVar

import orjson
from pydantic import BaseModel, ValidationError

from infinitecontex.task_execution.errors import PersistenceError
from infinitecontex.task_execution.models import (
    ActionJournalEntry,
    AuthorizationApproval,
    AuthorizationProposal,
    ExecutionGrant,
    ExecutionSession,
    GrantRevocation,
    GrantStateRecord,
)

T = TypeVar("T", bound=BaseModel)


class TaskExecutionStore:
    def __init__(self, plans_directory: Path) -> None:
        self.plans_directory = plans_directory

    def save_proposal(self, value: AuthorizationProposal) -> Path:
        return self._immutable(
            self._base(value.plan_id) / "execution-authorization-proposals", value.proposal_id, value
        )

    def save_approval(self, value: AuthorizationApproval) -> Path:
        return self._immutable(
            self._base(value.plan_id) / "execution-authorization-approvals", value.approval_id, value
        )

    def save_grant(self, value: ExecutionGrant, state: GrantStateRecord) -> Path:
        path = self._immutable(self._base(value.plan_id) / "execution-grants", value.grant_id, value)
        self.save_state(value.plan_id, state)
        return path

    def save_revocation(self, plan_id: str, value: GrantRevocation) -> Path:
        return self._immutable(self._base(plan_id) / "execution-grant-revocations", value.revocation_id, value)

    def save_state(self, plan_id: str, value: GrantStateRecord) -> Path:
        path = self._base(plan_id) / "execution-grants" / "state" / f"{value.grant_id}.json"
        self._replace(path, self.serialize(value))
        return path

    def save_session(self, value: ExecutionSession) -> Path:
        path = self._base(value.plan_id) / "execution-sessions" / value.session_id / "session.json"
        self._replace(path, self.serialize(value))
        return path

    def save_action(self, plan_id: str, session_id: str, value: ActionJournalEntry) -> Path:
        return self._immutable(
            self._base(plan_id) / "execution-sessions" / session_id / "actions", value.action_request_id, value
        )

    def load_proposal(self, plan_id: str, item_id: str) -> AuthorizationProposal:
        return self._load(
            self._base(plan_id) / "execution-authorization-proposals" / f"{item_id}.json", AuthorizationProposal
        )

    def load_approval(self, plan_id: str, item_id: str) -> AuthorizationApproval:
        return self._load(
            self._base(plan_id) / "execution-authorization-approvals" / f"{item_id}.json", AuthorizationApproval
        )

    def load_grant(self, plan_id: str, item_id: str) -> ExecutionGrant:
        return self._load(self._base(plan_id) / "execution-grants" / f"{item_id}.json", ExecutionGrant)

    def load_state(self, plan_id: str, grant_id: str) -> GrantStateRecord:
        return self._load(self._base(plan_id) / "execution-grants" / "state" / f"{grant_id}.json", GrantStateRecord)

    def load_session(self, plan_id: str, session_id: str) -> ExecutionSession:
        return self._load(self._base(plan_id) / "execution-sessions" / session_id / "session.json", ExecutionSession)

    def load_action(self, plan_id: str, session_id: str, action_id: str) -> ActionJournalEntry | None:
        path = self._base(plan_id) / "execution-sessions" / session_id / "actions" / f"{action_id}.json"
        return self._load(path, ActionJournalEntry) if path.exists() else None

    def list_proposals(self, plan_id: str) -> tuple[AuthorizationProposal, ...]:
        return self._list(self._base(plan_id) / "execution-authorization-proposals", AuthorizationProposal)

    def list_approvals(self, plan_id: str) -> tuple[AuthorizationApproval, ...]:
        return self._list(self._base(plan_id) / "execution-authorization-approvals", AuthorizationApproval)

    def list_grants(self, plan_id: str) -> tuple[ExecutionGrant, ...]:
        return self._list(self._base(plan_id) / "execution-grants", ExecutionGrant)

    def list_sessions(self, plan_id: str) -> tuple[ExecutionSession, ...]:
        directory = self._base(plan_id) / "execution-sessions"
        if not directory.exists():
            return ()
        return tuple(self._load(path, ExecutionSession) for path in sorted(directory.glob("*/session.json")))

    def list_actions(self, plan_id: str, session_id: str) -> tuple[ActionJournalEntry, ...]:
        return self._list(self._base(plan_id) / "execution-sessions" / session_id / "actions", ActionJournalEntry)

    @staticmethod
    def serialize(value: BaseModel) -> bytes:
        return orjson.dumps(
            value.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE
        )

    def _base(self, plan_id: str) -> Path:
        if not plan_id.startswith("plan-") or any(c not in "0123456789abcdef" for c in plan_id[5:]):
            raise PersistenceError("Invalid plan ID")
        return self.plans_directory / plan_id

    def _immutable(self, directory: Path, item_id: str, value: BaseModel) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{item_id}.json"
        payload = self.serialize(value)
        if target.exists():
            if target.read_bytes() != payload:
                raise PersistenceError(f"Immutable record collision for {item_id}")
            return target
        temporary = self._temporary(directory, target.name, payload)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_bytes() != payload:
                raise PersistenceError(f"Immutable record collision for {item_id}")
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def _replace(self, target: Path, payload: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._temporary(target.parent, target.name, payload)
        os.replace(temporary, target)

    @staticmethod
    def _temporary(directory: Path, name: str, payload: bytes) -> Path:
        fd, filename = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=directory)
        path = Path(filename)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return path

    @staticmethod
    def _load(path: Path, kind: type[T]) -> T:
        try:
            return kind.model_validate(orjson.loads(path.read_bytes()))
        except FileNotFoundError as exc:
            raise PersistenceError(f"Record {path.stem} was not found") from exc
        except (OSError, orjson.JSONDecodeError, ValidationError) as exc:
            raise PersistenceError(f"Record {path.name} is malformed or unsupported") from exc

    def _list(self, directory: Path, kind: type[T]) -> tuple[T, ...]:
        if not directory.exists():
            return ()
        return tuple(self._load(path, kind) for path in sorted(directory.glob("*.json")))

    def lock(self, plan_id: str, grant_id: str) -> Path:
        directory = self._base(plan_id) / "execution-grants" / "locks"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{grant_id}.lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError as exc:
            raise PersistenceError("Execution allowance is already reserved") from exc
        return path
