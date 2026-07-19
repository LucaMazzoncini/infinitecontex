"""Create-only atomic persistence for G6 transition artifacts."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import TypeVar

import orjson
from pydantic import BaseModel, ValidationError

from infinitecontex.state_transitions.errors import TransitionPersistenceError
from infinitecontex.state_transitions.models import (
    StateTransitionApplication,
    StateTransitionApproval,
    StateTransitionProposal,
)
from infinitecontex.state_transitions.policy import model_fingerprint

T = TypeVar("T", bound=BaseModel)
_IDS = {
    StateTransitionProposal: ("state-transition-proposal-", "proposal_id", "semantic_fingerprint", "created_at"),
    StateTransitionApproval: ("state-transition-approval-", "approval_id", "approval_fingerprint", "decided_at"),
    StateTransitionApplication: (
        "state-transition-application-",
        "application_id",
        "application_fingerprint",
        "applied_at",
    ),
}


class StateTransitionStore:
    def __init__(self, plans_directory: Path) -> None:
        self.plans_directory = plans_directory

    def save_proposal(self, value: StateTransitionProposal) -> Path:
        target = self._dir(value.plan_id, "state-transition-proposals") / f"{value.proposal_id}.json"
        return self._save(target, value)

    def save_approval(self, value: StateTransitionApproval) -> Path:
        prior = self.list_approvals(value.plan_id, proposal_id=value.proposal_id)
        if prior:
            if prior[0].approval_fingerprint == value.approval_fingerprint:
                return self._dir(value.plan_id, "state-transition-approvals") / f"{value.approval_id}.json"
            raise TransitionPersistenceError("Transition proposal already has an immutable decision")
        target = self._dir(value.plan_id, "state-transition-approvals") / f"{value.approval_id}.json"
        return self._save(target, value)

    def save_application(self, value: StateTransitionApplication) -> Path:
        prior = self.list_applications(value.plan_id, proposal_id=value.proposal_id)
        if prior:
            if prior[0].application_fingerprint == value.application_fingerprint:
                return self._dir(value.plan_id, "state-transition-applications") / f"{value.application_id}.json"
            raise TransitionPersistenceError("Transition proposal already has an application")
        target = self._dir(value.plan_id, "state-transition-applications") / f"{value.application_id}.json"
        return self._save(target, value)

    def load_proposal(self, plan_id: str, item_id: str) -> StateTransitionProposal:
        return self._load_id(plan_id, "state-transition-proposals", item_id, StateTransitionProposal)

    def load_approval(self, plan_id: str, item_id: str) -> StateTransitionApproval:
        return self._load_id(plan_id, "state-transition-approvals", item_id, StateTransitionApproval)

    def load_application(self, plan_id: str, item_id: str) -> StateTransitionApplication:
        return self._load_id(plan_id, "state-transition-applications", item_id, StateTransitionApplication)

    def list_proposals(self, plan_id: str) -> tuple[StateTransitionProposal, ...]:
        return self._list(plan_id, "state-transition-proposals", StateTransitionProposal)

    def list_approvals(self, plan_id: str, proposal_id: str | None = None) -> tuple[StateTransitionApproval, ...]:
        values = self._list(plan_id, "state-transition-approvals", StateTransitionApproval)
        return tuple(item for item in values if proposal_id is None or item.proposal_id == proposal_id)

    def list_applications(self, plan_id: str, proposal_id: str | None = None) -> tuple[StateTransitionApplication, ...]:
        values = self._list(plan_id, "state-transition-applications", StateTransitionApplication)
        return tuple(item for item in values if proposal_id is None or item.proposal_id == proposal_id)

    def _dir(self, plan_id: str, name: str) -> Path:
        if not re.fullmatch(r"plan-[0-9a-f]{24}", plan_id):
            raise TransitionPersistenceError("Invalid plan ID")
        return self.plans_directory / plan_id / name

    def _load_id(self, plan_id: str, name: str, item_id: str, model: type[T]) -> T:
        pattern = r"state-transition-(?:proposal|approval|application)-[0-9a-f]{24}"
        if not re.fullmatch(pattern, item_id):
            raise TransitionPersistenceError("Invalid transition record ID")
        return self._load(self._dir(plan_id, name) / f"{item_id}.json", model)

    def _list(self, plan_id: str, name: str, model: type[T]) -> tuple[T, ...]:
        root = self._dir(plan_id, name)
        return tuple(self._load(path, model) for path in sorted(root.glob("*.json"))) if root.exists() else ()

    @staticmethod
    def serialize(value: BaseModel) -> bytes:
        return orjson.dumps(
            value.model_dump(mode="json"),
            option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE,
        )

    def _save(self, target: Path, value: BaseModel) -> Path:
        self._verify(value)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            old = self._load(target, type(value))
            if old == value or (
                isinstance(old, StateTransitionProposal)
                and isinstance(value, StateTransitionProposal)
                and old.semantic_fingerprint == value.semantic_fingerprint
            ):
                return target
            raise TransitionPersistenceError(f"Immutable transition record {target.stem} already exists")
        descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(self.serialize(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, target)
        except OSError as exc:
            raise TransitionPersistenceError(f"Could not persist transition record: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def _load(self, path: Path, model: type[T]) -> T:
        try:
            value = model.model_validate(orjson.loads(path.read_bytes()))
        except (OSError, ValueError, ValidationError, orjson.JSONDecodeError) as exc:
            raise TransitionPersistenceError(f"Transition record {path.stem} is malformed or unsupported") from exc
        self._verify(value)
        return value

    @staticmethod
    def _verify(value: BaseModel) -> None:
        details = next((item for model, item in _IDS.items() if isinstance(value, model)), None)
        if details is None:
            raise TransitionPersistenceError("Unsupported transition record")
        prefix, id_field, fingerprint_field, timestamp_field = details
        fingerprint = model_fingerprint(value, fingerprint_field, id_field, timestamp_field)
        if getattr(value, fingerprint_field) != fingerprint or getattr(value, id_field) != prefix + fingerprint[:24]:
            raise TransitionPersistenceError("Transition record fingerprint is invalid")
