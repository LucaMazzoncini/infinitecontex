"""Create-only compact persistence for G2 tool execution records."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import orjson
from pydantic import ValidationError

from infinitecontex.tools.execution_errors import ExecutionPersistenceError, MalformedExecutionRecordError
from infinitecontex.tools.execution_fingerprints import record_fingerprint
from infinitecontex.tools.execution_models import EXECUTION_SCHEMA_VERSION, ToolExecutionRecord

_ID = re.compile(r"^tool-execution-[0-9a-f]{24}$")


class ToolExecutionStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, record: ToolExecutionRecord) -> Path:
        self._verify(record)
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{record.execution_id}.json"
        if target.exists():
            existing = self._load(target)
            if existing.record_fingerprint == record.record_fingerprint:
                return target
            raise ExecutionPersistenceError("Immutable execution ID collides with different content")
        descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=self.directory)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(self.serialize(record))
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, target)
        except OSError as exc:
            raise ExecutionPersistenceError(f"Could not persist immutable tool execution: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def load(self, execution_id: str) -> ToolExecutionRecord:
        self._validate_id(execution_id)
        path = self.directory / f"{execution_id}.json"
        if not path.exists():
            raise ExecutionPersistenceError(f"Execution {execution_id} was not found; run `infctx tool execution list`")
        return self._load(path)

    def list(self, *, limit: int = 100) -> tuple[ToolExecutionRecord, ...]:
        if not self.directory.exists():
            return ()
        values = tuple(self._load(path) for path in sorted(self.directory.glob("tool-execution-*.json")))
        return tuple(sorted(values, key=lambda item: (item.completed_at, item.execution_id), reverse=True)[:limit])

    @staticmethod
    def serialize(record: ToolExecutionRecord) -> bytes:
        return orjson.dumps(
            record.model_dump(mode="json"),
            option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE,
        )

    def _load(self, path: Path) -> ToolExecutionRecord:
        try:
            payload = orjson.loads(path.read_bytes())
            if not isinstance(payload, dict) or payload.get("schema_version") != EXECUTION_SCHEMA_VERSION:
                raise MalformedExecutionRecordError("Unsupported or malformed execution-record schema")
            value = ToolExecutionRecord.model_validate(payload)
            self._verify(value)
            return value
        except MalformedExecutionRecordError:
            raise
        except (OSError, ValueError, orjson.JSONDecodeError, ValidationError) as exc:
            raise MalformedExecutionRecordError(f"Execution record {path.name} is malformed; move it aside") from exc

    @staticmethod
    def _verify(record: ToolExecutionRecord) -> None:
        fingerprint = record_fingerprint(record)
        if record.record_fingerprint != fingerprint or record.execution_id != f"tool-execution-{fingerprint[:24]}":
            raise MalformedExecutionRecordError("Execution record fingerprint is invalid")

    @staticmethod
    def _validate_id(value: str) -> None:
        if not _ID.fullmatch(value):
            raise ExecutionPersistenceError("Invalid tool execution ID")
