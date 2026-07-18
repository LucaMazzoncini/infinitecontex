"""Create-only atomic persistence for G4 validation proposals, decisions, runs, and evidence."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import TypeVar

import orjson
from pydantic import BaseModel, ValidationError

from infinitecontex.tools.validation_definitions import (
    approval_fingerprint,
    evidence_fingerprint,
    proposal_fingerprint,
    record_fingerprint,
)
from infinitecontex.tools.validation_models import (
    ValidationApproval,
    ValidationEvidence,
    ValidationExecutionRecord,
    ValidationProposal,
)

_T = TypeVar("_T", bound=BaseModel)
_ID = re.compile(r"^[a-z-]+-[0-9a-f]{24}$")


class ValidationStore:
    def __init__(self, plans: Path, records: Path, evidence: Path) -> None:
        self.plans, self.records, self.evidence = plans, records, evidence

    def _plan(self, plan_id: str, kind: str) -> Path:
        return self.plans / plan_id / "validations" / kind

    def save_proposal(self, value: ValidationProposal) -> Path:
        self._verify(value)
        target = self._plan(value.plan_id, "proposals") / f"{value.proposal_id}.json"
        if (
            target.exists()
            and self.load_proposal(value.plan_id, value.proposal_id).semantic_fingerprint == value.semantic_fingerprint
        ):
            return target
        return self._save(target, value)

    def save_approval(self, value: ValidationApproval) -> Path:
        self._verify(value)
        existing = self.list_approvals(value.plan_id, value.proposal_id)
        if existing:
            if existing[0].approval_fingerprint == value.approval_fingerprint:
                return self._plan(value.plan_id, "approvals") / f"{value.approval_id}.json"
            raise ValueError("This validation proposal already has an immutable decision")
        return self._save(self._plan(value.plan_id, "approvals") / f"{value.approval_id}.json", value)

    def save_record(self, value: ValidationExecutionRecord) -> Path:
        self._verify(value)
        return self._save(self.records / f"{value.execution_id}.json", value)

    def save_evidence(self, value: ValidationEvidence) -> Path:
        self._verify(value)
        return self._save(self.evidence / f"{value.evidence_id}.json", value)

    def load_proposal(self, plan_id: str, value_id: str) -> ValidationProposal:
        return self._verified_load(self._plan(plan_id, "proposals") / f"{value_id}.json", ValidationProposal)

    def load_record(self, value_id: str) -> ValidationExecutionRecord:
        return self._verified_load(self.records / f"{value_id}.json", ValidationExecutionRecord)

    def load_evidence(self, value_id: str) -> ValidationEvidence:
        return self._verified_load(self.evidence / f"{value_id}.json", ValidationEvidence)

    def list_proposals(self, plan_id: str) -> tuple[ValidationProposal, ...]:
        directory = self._plan(plan_id, "proposals")
        return (
            ()
            if not directory.exists()
            else tuple(self.load_proposal(plan_id, p.stem) for p in sorted(directory.glob("*.json")))
        )

    def list_approvals(self, plan_id: str, proposal_id: str | None = None) -> tuple[ValidationApproval, ...]:
        directory = self._plan(plan_id, "approvals")
        if not directory.exists():
            return ()
        values = tuple(self._verified_load(p, ValidationApproval) for p in sorted(directory.glob("*.json")))
        return tuple(v for v in values if proposal_id is None or v.proposal_id == proposal_id)

    def list_records(self, limit: int = 100) -> tuple[ValidationExecutionRecord, ...]:
        if not self.records.exists():
            return ()
        values = tuple(self.load_record(p.stem) for p in sorted(self.records.glob("*.json")))
        return tuple(sorted(values, key=lambda v: (v.completed_at, v.execution_id), reverse=True)[:limit])

    def list_evidence(self, limit: int = 100) -> tuple[ValidationEvidence, ...]:
        if not self.evidence.exists():
            return ()
        values = tuple(self.load_evidence(p.stem) for p in sorted(self.evidence.glob("*.json")))
        return tuple(sorted(values, key=lambda v: (v.created_at, v.evidence_id), reverse=True)[:limit])

    @staticmethod
    def serialize(value: BaseModel) -> bytes:
        return orjson.dumps(
            value.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE
        )

    @classmethod
    def _save(cls, target: Path, value: BaseModel, idempotent: bool = False) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if idempotent and target.read_bytes() == cls.serialize(value):
                return target
            raise ValueError(f"Immutable validation record {target.stem} already exists")
        fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(cls.serialize(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    @classmethod
    def _verified_load(cls, path: Path, model: type[_T]) -> _T:
        if not _ID.fullmatch(path.stem) or not path.exists():
            raise ValueError(f"Validation record {path.stem} was not found")
        try:
            value = model.model_validate(orjson.loads(path.read_bytes()))
        except (OSError, ValueError, ValidationError, orjson.JSONDecodeError) as exc:
            raise ValueError(f"Validation record {path.name} is malformed or unsupported") from exc
        cls._verify(value)
        return value

    @staticmethod
    def _verify(value: BaseModel) -> None:
        if isinstance(value, ValidationProposal):
            fp, actual, prefix = proposal_fingerprint(value), value.semantic_fingerprint, "validation-proposal-"
            value_id = value.proposal_id
        elif isinstance(value, ValidationApproval):
            fp, actual, prefix = approval_fingerprint(value), value.approval_fingerprint, "validation-approval-"
            value_id = value.approval_id
        elif isinstance(value, ValidationExecutionRecord):
            fp, actual, prefix = record_fingerprint(value), value.record_fingerprint, "tool-validation-"
            value_id = value.execution_id
        elif isinstance(value, ValidationEvidence):
            fp, actual, prefix = evidence_fingerprint(value), value.fingerprint, "validation-evidence-"
            value_id = value.evidence_id
        else:
            raise ValueError("Unsupported validation record")
        if actual != fp or value_id != prefix + fp[:24]:
            raise ValueError("Validation record fingerprint is invalid")
