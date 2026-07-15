"""Immutable plan revisions and atomic current-pointer persistence."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import orjson
from pydantic import ValidationError

from infinitecontex.planning.errors import (
    PlanFormatError,
    PlanNotFoundError,
    PlanPersistenceError,
    PlanRevisionNotFoundError,
)
from infinitecontex.planning.models import PLAN_SCHEMA_VERSION, CurrentPlanPointer, PlanRevision
from infinitecontex.planning.normalization import verify_revision_integrity

_SAFE_PLAN_ID = re.compile(r"^plan-[0-9a-f]{24}$")


def migrate_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise PlanFormatError(
            f"Unsupported plan schema version {payload.get('schema_version')!r}; upgrade Infinite Context and retry"
        )
    return payload


class PlanStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save_revision(self, revision: PlanRevision) -> Path:
        try:
            verify_revision_integrity(revision)
        except ValueError as exc:
            raise PlanPersistenceError(f"Plan revision fingerprints are invalid: {exc}") from exc
        plan_dir = self._plan_dir(revision.plan_id)
        pointer_path = plan_dir / "current.json"
        if pointer_path.exists():
            current = self.load_current(revision.plan_id)
            if revision.current_revision != current.current_revision + 1:
                raise PlanPersistenceError("A new plan revision must increment the current revision by exactly one")
            if revision.previous_revision_fingerprint != current.revision_fingerprint:
                raise PlanPersistenceError("A new plan revision must reference the exact current revision fingerprint")
        elif revision.current_revision != 1 or revision.previous_revision_fingerprint is not None:
            raise PlanPersistenceError("The first persisted plan revision must be revision 1 without a predecessor")
        revisions = plan_dir / "revisions"
        revisions.mkdir(parents=True, exist_ok=True)
        target = revisions / f"{revision.current_revision:06d}.json"
        if target.exists():
            raise PlanPersistenceError(f"Revision {revision.current_revision} already exists and is immutable")
        temporary = self._write_temporary(revisions, target.name, self.serialize_revision(revision))
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise PlanPersistenceError(f"Revision {revision.current_revision} already exists and is immutable") from exc
        except OSError as exc:
            raise PlanPersistenceError(f"Could not persist immutable plan revision: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        pointer = CurrentPlanPointer(
            plan_id=revision.plan_id,
            current_revision=revision.current_revision,
            revision_fingerprint=revision.revision_fingerprint,
            graph_fingerprint=revision.graph_fingerprint,
            updated_at=revision.updated_at,
        )
        self._replace(plan_dir / "current.json", self.serialize_pointer(pointer))
        return target

    def load_current(self, plan_id: str) -> PlanRevision:
        pointer_path = self._plan_dir(plan_id) / "current.json"
        if not pointer_path.exists():
            raise PlanNotFoundError(f"Plan {plan_id} was not found; run `infctx plan list`")
        pointer = self._load_pointer(pointer_path)
        revision = self.load_revision(plan_id, pointer.current_revision)
        if revision.revision_fingerprint != pointer.revision_fingerprint:
            raise PlanFormatError(f"Current pointer for {plan_id} does not match its immutable revision")
        return revision

    def load_revision(self, plan_id: str, revision: int) -> PlanRevision:
        path = self._plan_dir(plan_id) / "revisions" / f"{revision:06d}.json"
        if revision < 1 or not path.exists():
            raise PlanRevisionNotFoundError(f"Plan {plan_id} revision {revision} was not found")
        loaded = self._load_revision(path)
        if revision > 1:
            previous_path = path.parent / f"{revision - 1:06d}.json"
            if not previous_path.exists():
                raise PlanFormatError(f"Plan {plan_id} revision {revision} has no preceding immutable revision")
            previous = self._load_revision(previous_path)
            if loaded.previous_revision_fingerprint != previous.revision_fingerprint:
                raise PlanFormatError(f"Plan {plan_id} revision {revision} references the wrong previous fingerprint")
        return loaded

    def history(self, plan_id: str) -> list[PlanRevision]:
        revisions = self._plan_dir(plan_id) / "revisions"
        if not revisions.exists():
            raise PlanNotFoundError(f"Plan {plan_id} was not found; run `infctx plan list`")
        history = [self._load_revision(path) for path in sorted(revisions.glob("*.json"))]
        for previous, current in zip(history, history[1:], strict=False):
            if current.current_revision != previous.current_revision + 1:
                raise PlanFormatError(f"Plan {plan_id} revision history is not contiguous")
            if current.previous_revision_fingerprint != previous.revision_fingerprint:
                raise PlanFormatError(f"Plan {plan_id} revision history has a predecessor fingerprint mismatch")
        return history

    def list_current(self) -> list[PlanRevision]:
        if not self.directory.exists():
            return []
        plans = [self.load_current(path.name) for path in sorted(self.directory.iterdir()) if path.is_dir()]
        return sorted(plans, key=lambda item: (item.updated_at, item.plan_id), reverse=True)

    @staticmethod
    def serialize_revision(revision: PlanRevision) -> bytes:
        return orjson.dumps(
            revision.model_dump(mode="json"),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE,
        )

    @staticmethod
    def serialize_pointer(pointer: CurrentPlanPointer) -> bytes:
        return orjson.dumps(
            pointer.model_dump(mode="json"),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE,
        )

    def _load_revision(self, path: Path) -> PlanRevision:
        try:
            raw = orjson.loads(path.read_bytes())
            if not isinstance(raw, dict):
                raise ValueError("revision must be a JSON object")
            revision = PlanRevision.model_validate(migrate_plan_payload(raw))
            verify_revision_integrity(revision)
            return revision
        except PlanFormatError:
            raise
        except (OSError, ValueError, orjson.JSONDecodeError, ValidationError) as exc:
            raise PlanFormatError(f"Plan revision {path.name} is malformed; move it aside and re-import") from exc

    def _load_pointer(self, path: Path) -> CurrentPlanPointer:
        try:
            raw = orjson.loads(path.read_bytes())
            return CurrentPlanPointer.model_validate(raw)
        except (OSError, orjson.JSONDecodeError, ValidationError) as exc:
            raise PlanFormatError(f"Plan pointer {path} is malformed; restore it from revision history") from exc

    def _plan_dir(self, plan_id: str) -> Path:
        if not _SAFE_PLAN_ID.fullmatch(plan_id):
            raise PlanNotFoundError("Invalid plan ID; use an ID returned by `infctx plan list`")
        return self.directory / plan_id

    @staticmethod
    def _write_temporary(directory: Path, name: str, content: bytes) -> Path:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return temporary

    def _replace(self, target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._write_temporary(target.parent, target.name, content)
        try:
            os.replace(temporary, target)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise PlanPersistenceError(f"Could not update current plan pointer: {exc}") from exc
