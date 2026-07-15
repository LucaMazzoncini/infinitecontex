"""Immutable task-context analyses and atomic current pointers."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import orjson
from pydantic import ValidationError

from infinitecontex.task_context.errors import TaskContextNotFoundError, TaskContextPersistenceError
from infinitecontex.task_context.fingerprints import compute_analysis_fingerprint
from infinitecontex.task_context.models import (
    TASK_CONTEXT_ANALYSIS_SCHEMA_VERSION,
    TaskContextAnalysis,
    TaskContextCurrentPointer,
)

_PLAN = re.compile(r"^plan-[0-9a-f]{24}$")
_TASK = re.compile(r"^task-[0-9a-f]{24}$")
_ANALYSIS = re.compile(r"^task-context-[0-9a-f]{24}$")


def migrate_analysis_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != TASK_CONTEXT_ANALYSIS_SCHEMA_VERSION:
        raise TaskContextPersistenceError(
            f"Unsupported task-context analysis schema {payload.get('schema_version')!r}; upgrade Infinite Context"
        )
    return payload


class TaskContextAnalysisStore:
    def __init__(self, plans_directory: Path) -> None:
        self.plans_directory = plans_directory

    def save(self, analysis: TaskContextAnalysis) -> Path:
        self._verify(analysis)
        directory = self._analysis_directory(analysis.plan_id, analysis.plan_revision)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{analysis.analysis_id}.json"
        if target.exists():
            existing = self._load(target)
            if existing.semantic_fingerprint != analysis.semantic_fingerprint:
                raise TaskContextPersistenceError("Immutable analysis ID collides with different semantic content")
        else:
            temporary = self._write_temporary(directory, target.name, self.serialize(analysis))
            try:
                os.link(temporary, target)
            except FileExistsError:
                existing = self._load(target)
                if existing.semantic_fingerprint != analysis.semantic_fingerprint:
                    raise TaskContextPersistenceError(
                        "Immutable analysis was created concurrently with different content"
                    )
            except OSError as exc:
                raise TaskContextPersistenceError(f"Could not persist immutable task-context analysis: {exc}") from exc
            finally:
                temporary.unlink(missing_ok=True)
        pointer = TaskContextCurrentPointer(
            plan_id=analysis.plan_id,
            plan_revision=analysis.plan_revision,
            task_id=analysis.task_id,
            analysis_id=analysis.analysis_id,
            semantic_fingerprint=analysis.semantic_fingerprint,
            updated_at=analysis.created_at,
        )
        pointer_path = directory / "current" / f"{analysis.task_id}.json"
        self._replace(pointer_path, self.serialize_pointer(pointer))
        return target

    def load(self, plan_id: str, revision: int, analysis_id: str) -> TaskContextAnalysis:
        self._validate_ids(plan_id, analysis_id=analysis_id)
        path = self._analysis_directory(plan_id, revision) / f"{analysis_id}.json"
        if not path.exists():
            raise TaskContextNotFoundError(f"Task-context analysis {analysis_id} was not found")
        return self._load(path)

    def load_current(self, plan_id: str, revision: int, task_id: str) -> TaskContextAnalysis:
        self._validate_ids(plan_id, task_id=task_id)
        path = self._analysis_directory(plan_id, revision) / "current" / f"{task_id}.json"
        if not path.exists():
            raise TaskContextNotFoundError(f"No current task-context analysis exists for {task_id}")
        try:
            pointer = TaskContextCurrentPointer.model_validate(orjson.loads(path.read_bytes()))
        except (OSError, orjson.JSONDecodeError, ValidationError) as exc:
            raise TaskContextPersistenceError(f"Task-context pointer {path.name} is malformed") from exc
        analysis = self.load(plan_id, revision, pointer.analysis_id)
        if analysis.semantic_fingerprint != pointer.semantic_fingerprint or analysis.task_id != task_id:
            raise TaskContextPersistenceError("Task-context current pointer does not match its immutable analysis")
        return analysis

    def list(self, plan_id: str, revision: int) -> list[TaskContextAnalysis]:
        self._validate_ids(plan_id)
        directory = self._analysis_directory(plan_id, revision)
        if not directory.exists():
            return []
        analyses = [self._load(path) for path in sorted(directory.glob("task-context-*.json"))]
        return sorted(analyses, key=lambda item: (item.created_at, item.analysis_id), reverse=True)

    @staticmethod
    def serialize(analysis: TaskContextAnalysis) -> bytes:
        return orjson.dumps(
            analysis.model_dump(mode="json"),
            option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE,
        )

    @staticmethod
    def serialize_pointer(pointer: TaskContextCurrentPointer) -> bytes:
        return orjson.dumps(
            pointer.model_dump(mode="json"),
            option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE,
        )

    def _load(self, path: Path) -> TaskContextAnalysis:
        try:
            raw = orjson.loads(path.read_bytes())
            if not isinstance(raw, dict):
                raise ValueError("analysis must be an object")
            analysis = TaskContextAnalysis.model_validate(migrate_analysis_payload(raw))
            self._verify(analysis)
            return analysis
        except TaskContextPersistenceError:
            raise
        except (OSError, ValueError, orjson.JSONDecodeError, ValidationError) as exc:
            raise TaskContextPersistenceError(f"Task-context analysis {path.name} is malformed; move it aside") from exc

    @staticmethod
    def _verify(analysis: TaskContextAnalysis) -> None:
        fingerprint = compute_analysis_fingerprint(analysis)
        if fingerprint != analysis.semantic_fingerprint:
            raise TaskContextPersistenceError("Task-context analysis semantic fingerprint does not match its content")
        if analysis.analysis_id != f"task-context-{fingerprint[:24]}":
            raise TaskContextPersistenceError("Task-context analysis ID is inconsistent with its fingerprint")

    def _analysis_directory(self, plan_id: str, revision: int) -> Path:
        self._validate_ids(plan_id)
        if revision < 1:
            raise TaskContextPersistenceError("Plan revision must be positive")
        return self.plans_directory / plan_id / "analyses" / f"{revision:06d}" / "task-context"

    @staticmethod
    def _validate_ids(plan_id: str, task_id: str | None = None, analysis_id: str | None = None) -> None:
        if not _PLAN.fullmatch(plan_id):
            raise TaskContextPersistenceError("Invalid plan ID")
        if task_id is not None and not _TASK.fullmatch(task_id):
            raise TaskContextPersistenceError("Invalid task ID")
        if analysis_id is not None and not _ANALYSIS.fullmatch(analysis_id):
            raise TaskContextPersistenceError("Invalid task-context analysis ID")

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
            raise TaskContextPersistenceError(f"Could not update task-context current pointer: {exc}") from exc
