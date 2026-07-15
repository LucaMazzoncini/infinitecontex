"""Atomic deterministic JSON persistence for model profiles."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import orjson
from pydantic import ValidationError

from infinitecontex.model_profiles.errors import (
    ModelProfileDigestMismatchError,
    ModelProfileFormatError,
    ModelProfileNotFoundError,
)
from infinitecontex.model_profiles.models import MODEL_PROFILE_SCHEMA_VERSION, ModelProfile, normalize_model_name


def migrate_profile_payload(payload: dict[str, Any]) -> dict[str, Any]:
    version = payload.get("schema_version")
    if version != MODEL_PROFILE_SCHEMA_VERSION:
        raise ModelProfileFormatError(
            f"Unsupported model profile schema version {version!r}; upgrade Infinite Context and retry"
        )
    return payload


class ModelProfileStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, profile: ModelProfile) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / self._filename(profile)
        content = self.serialize(profile)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=self.directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return target

    def list_profiles(self) -> list[ModelProfile]:
        if not self.directory.exists():
            return []
        return [self._load_path(path) for path in sorted(self.directory.glob("*.json"))]

    def find_exact(self, provider: str, model_name: str, digest: str | None) -> ModelProfile:
        if digest is None:
            raise ModelProfileNotFoundError(
                f"No exact profile lookup is possible for {provider}/{model_name} without a model digest"
            )
        normalized = normalize_model_name(model_name)
        candidates = [
            profile
            for profile in self.list_profiles()
            if profile.model_identity.provider.casefold() == provider.casefold()
            and profile.model_identity.normalized_model_name == normalized
        ]
        for profile in candidates:
            if profile.model_identity.model_digest == digest:
                return profile
        if candidates and digest is not None:
            raise ModelProfileDigestMismatchError(
                f"A profile exists for {model_name}, but not digest {digest}; create a profile for this model build"
            )
        raise ModelProfileNotFoundError(f"No exact profile exists for {provider}/{model_name}")

    def find_by_name(self, provider: str, model_name: str) -> list[ModelProfile]:
        normalized = normalize_model_name(model_name)
        return [
            profile
            for profile in self.list_profiles()
            if profile.model_identity.provider.casefold() == provider.casefold()
            and profile.model_identity.normalized_model_name == normalized
        ]

    def find_by_id(self, profile_id: str) -> ModelProfile:
        matches = [profile for profile in self.list_profiles() if profile.profile_id == profile_id]
        if not matches:
            raise ModelProfileNotFoundError(f"No persisted model profile has ID {profile_id}")
        if len(matches) > 1:
            raise ModelProfileFormatError(f"Model profile ID {profile_id} is duplicated; move stale copies aside")
        return matches[0]

    @staticmethod
    def serialize(profile: ModelProfile) -> bytes:
        return orjson.dumps(
            profile.model_dump(mode="json"),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE,
        )

    def _load_path(self, path: Path) -> ModelProfile:
        try:
            raw = orjson.loads(path.read_bytes())
            if not isinstance(raw, dict):
                raise ModelProfileFormatError(f"Model profile {path.name} must contain a JSON object")
            payload = migrate_profile_payload(raw)
            return ModelProfile.model_validate(payload)
        except ModelProfileFormatError:
            raise
        except (OSError, orjson.JSONDecodeError, ValidationError) as exc:
            raise ModelProfileFormatError(
                f"Model profile {path.name} is malformed; move it aside and recreate the profile"
            ) from exc

    @staticmethod
    def _filename(profile: ModelProfile) -> str:
        identity = profile.model_identity
        identity_key = identity.model_digest or "weak-unverified"
        raw = f"{identity.provider.casefold()}\0{identity.normalized_model_name}\0{identity_key}"
        suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
        return f"profile-{suffix}.json"
