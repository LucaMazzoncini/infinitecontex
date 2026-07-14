"""Atomic compact admission-record persistence."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import orjson
from pydantic import ValidationError

from infinitecontex.context_admission.errors import AdmissionRecordPersistenceError
from infinitecontex.context_admission.models import ADMISSION_RECORD_SCHEMA_VERSION, AdmissionRecord

_SAFE_ID = re.compile(r"^context-admission-[0-9a-f]{24}$")


def migrate_admission_record_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != ADMISSION_RECORD_SCHEMA_VERSION:
        raise AdmissionRecordPersistenceError(
            f"Unsupported admission record schema {payload.get('schema_version')!r}; upgrade Infinite Context"
        )
    return payload


class AdmissionRecordStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, record: AdmissionRecord) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / self._filename(record.admission_id)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=self.directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(self.serialize(record))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise AdmissionRecordPersistenceError(f"Could not persist admission record: {exc}") from exc
        return target

    def load(self, admission_id: str) -> AdmissionRecord:
        path = self.directory / self._filename(admission_id)
        if not path.exists():
            raise AdmissionRecordPersistenceError(f"Admission record {admission_id} was not found")
        return self._load(path)

    def list_records(self) -> list[AdmissionRecord]:
        if not self.directory.exists():
            return []
        return sorted(
            (self._load(path) for path in self.directory.glob("*.json")),
            key=lambda item: (item.timestamp, item.admission_id),
            reverse=True,
        )

    @staticmethod
    def serialize(record: AdmissionRecord) -> bytes:
        return orjson.dumps(
            record.model_dump(mode="json"),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE,
        )

    def _load(self, path: Path) -> AdmissionRecord:
        try:
            raw = orjson.loads(path.read_bytes())
            if not isinstance(raw, dict):
                raise ValueError("record is not an object")
            return AdmissionRecord.model_validate(migrate_admission_record_payload(raw))
        except AdmissionRecordPersistenceError:
            raise
        except (OSError, ValueError, orjson.JSONDecodeError, ValidationError) as exc:
            raise AdmissionRecordPersistenceError(f"Admission record {path.name} is malformed; move it aside") from exc

    @staticmethod
    def _filename(admission_id: str) -> str:
        if not _SAFE_ID.fullmatch(admission_id):
            raise AdmissionRecordPersistenceError("Invalid admission record ID")
        return f"{admission_id}.json"
