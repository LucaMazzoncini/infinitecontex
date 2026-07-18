"""Create-only atomic persistence for G3 proposals, approvals, and records."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import TypeVar

import orjson
from pydantic import BaseModel, ValidationError

from infinitecontex.tools.mutation_errors import MutationPersistenceError
from infinitecontex.tools.mutation_fingerprints import (
    approval_fingerprint,
    mutation_record_fingerprint,
    proposal_fingerprint,
)
from infinitecontex.tools.mutation_models import MutationApproval, MutationExecutionRecord, MutationProposal

_T = TypeVar("_T", bound=BaseModel)
_IDS = {
    "proposal": re.compile(r"^mutation-proposal-[0-9a-f]{24}$"),
    "approval": re.compile(r"^mutation-approval-[0-9a-f]{24}$"),
    "record": re.compile(r"^tool-mutation-[0-9a-f]{24}$"),
}


class MutationStore:
    def __init__(self, plans_directory: Path, records_directory: Path) -> None:
        self.plans_directory = plans_directory
        self.records_directory = records_directory

    def save_proposal(self, value: MutationProposal) -> Path:
        self._verify(value)
        target = self._plan_dir(value.plan_id, "proposals") / f"{value.proposal_id}.json"
        if target.exists():
            existing = self.load_proposal(value.plan_id, value.proposal_id)
            if existing.semantic_fingerprint == value.semantic_fingerprint:
                return target
        return self._save(target, value)

    def save_approval(self, value: MutationApproval) -> Path:
        self._verify(value)
        existing = self.list_approvals(value.plan_id, proposal_id=value.proposal_id)
        if existing:
            if existing[0].approval_fingerprint == value.approval_fingerprint:
                return self._plan_dir(value.plan_id, "approvals") / f"{value.approval_id}.json"
            raise MutationPersistenceError("This mutation proposal already has an immutable human decision")
        return self._save(self._plan_dir(value.plan_id, "approvals") / f"{value.approval_id}.json", value)

    def save_record(self, value: MutationExecutionRecord) -> Path:
        self._verify(value)
        return self._save(self.records_directory / f"{value.mutation_id}.json", value)

    def load_proposal(self, plan_id: str, value_id: str) -> MutationProposal:
        self._validate_id("proposal", value_id)
        value = self._load(self._plan_dir(plan_id, "proposals") / f"{value_id}.json", MutationProposal)
        self._verify(value)
        return value

    def load_approval(self, plan_id: str, value_id: str) -> MutationApproval:
        self._validate_id("approval", value_id)
        value = self._load(self._plan_dir(plan_id, "approvals") / f"{value_id}.json", MutationApproval)
        self._verify(value)
        return value

    def load_record(self, value_id: str) -> MutationExecutionRecord:
        self._validate_id("record", value_id)
        value = self._load(self.records_directory / f"{value_id}.json", MutationExecutionRecord)
        self._verify(value)
        return value

    def list_proposals(self, plan_id: str) -> tuple[MutationProposal, ...]:
        directory = self._plan_dir(plan_id, "proposals")
        if not directory.exists():
            return ()
        return tuple(self.load_proposal(plan_id, item.stem) for item in sorted(directory.glob("*.json")))

    def list_approvals(self, plan_id: str, *, proposal_id: str | None = None) -> tuple[MutationApproval, ...]:
        directory = self._plan_dir(plan_id, "approvals")
        if not directory.exists():
            return ()
        values = tuple(self.load_approval(plan_id, item.stem) for item in sorted(directory.glob("*.json")))
        return tuple(item for item in values if proposal_id is None or item.proposal_id == proposal_id)

    def list_records(self, *, limit: int = 100) -> tuple[MutationExecutionRecord, ...]:
        if not self.records_directory.exists():
            return ()
        values = tuple(self.load_record(item.stem) for item in sorted(self.records_directory.glob("*.json")))
        return tuple(sorted(values, key=lambda item: (item.completed_at, item.mutation_id), reverse=True)[:limit])

    def _plan_dir(self, plan_id: str, kind: str) -> Path:
        return self.plans_directory / plan_id / "mutations" / kind

    @staticmethod
    def serialize(value: BaseModel) -> bytes:
        return orjson.dumps(
            value.model_dump(mode="json"),
            option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE,
        )

    @classmethod
    def _save(cls, target: Path, value: BaseModel) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() == cls.serialize(value):
                return target
            raise MutationPersistenceError(f"Immutable mutation record {target.stem} already exists")
        descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(cls.serialize(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, target)
        except OSError as exc:
            raise MutationPersistenceError(f"Could not persist immutable mutation record: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return target

    @staticmethod
    def _load(path: Path, model: type[_T]) -> _T:
        if not path.exists():
            raise MutationPersistenceError(f"Mutation record {path.stem} was not found")
        try:
            return model.model_validate(orjson.loads(path.read_bytes()))
        except (OSError, ValueError, ValidationError, orjson.JSONDecodeError) as exc:
            raise MutationPersistenceError(f"Mutation record {path.name} is malformed or unsupported") from exc

    @staticmethod
    def _verify(value: BaseModel) -> None:
        if isinstance(value, MutationProposal):
            valid = proposal_fingerprint(value)
            actual, expected_id = value.semantic_fingerprint, f"mutation-proposal-{valid[:24]}"
            actual_id = value.proposal_id
        elif isinstance(value, MutationApproval):
            valid = approval_fingerprint(value)
            actual, expected_id = value.approval_fingerprint, f"mutation-approval-{valid[:24]}"
            actual_id = value.approval_id
        elif isinstance(value, MutationExecutionRecord):
            valid = mutation_record_fingerprint(value)
            actual, expected_id = value.record_fingerprint, f"tool-mutation-{valid[:24]}"
            actual_id = value.mutation_id
        else:
            raise MutationPersistenceError("Unsupported mutation record type")
        if actual != valid or actual_id != expected_id:
            raise MutationPersistenceError("Mutation record fingerprint is invalid")

    @staticmethod
    def _validate_id(kind: str, value: str) -> None:
        if not _IDS[kind].fullmatch(value):
            raise MutationPersistenceError(f"Invalid mutation {kind} ID")
