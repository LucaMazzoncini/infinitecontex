"""Atomic deterministic persistence for context manifests."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import orjson
from pydantic import ValidationError

from infinitecontex.context_packing.errors import ContextManifestFormatError, ContextManifestNotFoundError
from infinitecontex.context_packing.models import CONTEXT_MANIFEST_SCHEMA_VERSION, ContextManifest

_SAFE_ID = re.compile(r"^context-manifest-[0-9a-f]{24}$")


def migrate_manifest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    version = payload.get("schema_version")
    if version != CONTEXT_MANIFEST_SCHEMA_VERSION:
        raise ContextManifestFormatError(
            f"Unsupported context manifest schema version {version!r}; upgrade Infinite Context and retry"
        )
    return payload


class ContextManifestStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, manifest: ContextManifest) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / self._filename(manifest.manifest_id)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=self.directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(self.serialize(manifest))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return target

    def list_manifests(self) -> list[ContextManifest]:
        if not self.directory.exists():
            return []
        manifests = [self._load_path(path) for path in sorted(self.directory.glob("*.json"))]
        return sorted(manifests, key=lambda item: (item.calculated_at, item.manifest_id), reverse=True)

    def load(self, manifest_id: str) -> ContextManifest:
        path = self.directory / self._filename(manifest_id)
        if not path.exists():
            raise ContextManifestNotFoundError(
                f"Context manifest {manifest_id} was not found; run `infctx context manifest list`"
            )
        return self._load_path(path)

    @staticmethod
    def serialize(manifest: ContextManifest) -> bytes:
        return orjson.dumps(
            manifest.model_dump(mode="json"),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE,
        )

    def _load_path(self, path: Path) -> ContextManifest:
        try:
            raw = orjson.loads(path.read_bytes())
            if not isinstance(raw, dict):
                raise ContextManifestFormatError(f"Context manifest {path.name} must contain a JSON object")
            return ContextManifest.model_validate(migrate_manifest_payload(raw))
        except ContextManifestFormatError:
            raise
        except (OSError, orjson.JSONDecodeError, ValidationError) as exc:
            raise ContextManifestFormatError(
                f"Context manifest {path.name} is malformed; move it aside and recreate the manifest"
            ) from exc

    @staticmethod
    def _filename(manifest_id: str) -> str:
        if not _SAFE_ID.fullmatch(manifest_id):
            raise ContextManifestNotFoundError("Invalid context manifest ID; use an ID returned by manifest list")
        return f"{manifest_id}.json"
