"""Immutable local persistence for split proposals and approvals."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import TypeVar

import orjson
from pydantic import BaseModel, ValidationError

from infinitecontex.task_splitting.errors import SplitNotFoundError, SplitPersistenceError
from infinitecontex.task_splitting.fingerprints import approval_fingerprint, proposal_fingerprint
from infinitecontex.task_splitting.models import SplitApproval, SplitProposal

_T = TypeVar("_T", bound=BaseModel)
_PROPOSAL_ID = re.compile(r"^split-proposal-[0-9a-f]{24}$")
_APPROVAL_ID = re.compile(r"^split-approval-[0-9a-f]{24}$")


class TaskSplitStore:
    def __init__(self, plans_directory: Path) -> None:
        self.plans_directory = plans_directory

    def save_proposal(self, value: SplitProposal) -> Path:
        if proposal_fingerprint(value) != value.semantic_fingerprint:
            raise SplitPersistenceError("Split proposal fingerprint is invalid; recreate it")
        target = self._directory(value.plan_id, "proposals") / f"{value.proposal_id}.json"
        if target.exists():
            existing = self.load_proposal(value.plan_id, value.proposal_id)
            if existing.semantic_fingerprint == value.semantic_fingerprint:
                return target
            raise SplitPersistenceError(f"Split proposal ID {value.proposal_id} already exists with different content")
        return self._save(target, value)

    def load_proposal(self, plan_id: str, record_id: str) -> SplitProposal:
        if not _PROPOSAL_ID.fullmatch(record_id):
            raise SplitNotFoundError("Invalid proposal ID; use an ID returned by the split service")
        path = self._directory(plan_id, "proposals") / f"{record_id}.json"
        value = self._load(path, SplitProposal)
        if proposal_fingerprint(value) != value.semantic_fingerprint:
            raise SplitPersistenceError("Stored proposal failed integrity verification; recreate it")
        return value

    def save_approval(self, value: SplitApproval) -> Path:
        if approval_fingerprint(value) != value.approval_fingerprint:
            raise SplitPersistenceError("Split approval fingerprint is invalid; recreate it")
        target = self._directory(value.plan_id, "approvals") / f"{value.approval_id}.json"
        return self._save(target, value)

    def load_approval(self, plan_id: str, record_id: str) -> SplitApproval:
        if not _APPROVAL_ID.fullmatch(record_id):
            raise SplitNotFoundError("Invalid approval ID; use an ID returned by the split service")
        path = self._directory(plan_id, "approvals") / f"{record_id}.json"
        value = self._load(path, SplitApproval)
        if approval_fingerprint(value) != value.approval_fingerprint:
            raise SplitPersistenceError("Stored approval failed integrity verification; recreate it")
        return value

    def list_proposals(self, plan_id: str) -> tuple[SplitProposal, ...]:
        directory = self._directory(plan_id, "proposals")
        if not directory.exists():
            return ()
        return tuple(self.load_proposal(plan_id, path.stem) for path in sorted(directory.glob("*.json")))

    def list_approvals(self, plan_id: str) -> tuple[SplitApproval, ...]:
        directory = self._directory(plan_id, "approvals")
        if not directory.exists():
            return ()
        return tuple(self.load_approval(plan_id, path.stem) for path in sorted(directory.glob("*.json")))

    def _directory(self, plan_id: str, kind: str) -> Path:
        return self.plans_directory / plan_id / "splits" / kind

    @staticmethod
    def _load(path: Path, model: type[_T]) -> _T:
        if not path.exists():
            raise SplitNotFoundError("Split record was not found; recreate or list split records")
        try:
            return model.model_validate(orjson.loads(path.read_bytes()))
        except (OSError, orjson.JSONDecodeError, ValidationError, ValueError) as exc:
            raise SplitPersistenceError("Stored split record is malformed; move it aside and recreate it") from exc

    @staticmethod
    def _save(target: Path, value: BaseModel) -> Path:
        content = orjson.dumps(
            value.model_dump(mode="json"),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise SplitPersistenceError(f"Split record {target.stem} already exists and is immutable") from exc
            except OSError as exc:
                raise SplitPersistenceError(f"Could not persist immutable split record: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return target
