"""Idempotent first-slice setup for repository state and Ollama."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson
from pydantic import BaseModel, Field

from infinitecontex.llm.base import LLMClient
from infinitecontex.llm.errors import LLMError
from infinitecontex.model_profiles.errors import (
    ModelProfileDigestMismatchError,
    ModelProfileNotFoundError,
)
from infinitecontex.model_profiles.models import CalibrationStatus, IdentityStrength
from infinitecontex.model_profiles.service import ModelProfileService
from infinitecontex.model_profiles.store import ModelProfileStore
from infinitecontex.service import InfiniteContextService
from infinitecontex.storage.layout import build_layout

PRIMARY_MODEL = "qwen3.6:35b"
FALLBACK_MODEL = "qwen3-coder:30b"


class SetupReport(BaseModel):
    schema_version: int = 1
    project_root: str
    state: str
    ollama_available: bool
    ollama_version: str | None = None
    installed_models: list[str] = Field(default_factory=list)
    recommended_model: str
    recommendation_installed: bool
    config_written: bool = False
    profile_status: str
    profile_id: str | None = None
    ready: bool
    message: str


class SetupService:
    def __init__(self, project_root: Path, client: LLMClient) -> None:
        self.project_root = discover_repository(project_root)
        self.client = client

    def run(self, *, check_only: bool, allow_config_write: bool) -> SetupReport:
        state = "existing" if (self.project_root / ".infctx").exists() else "missing"
        available = False
        version: str | None = None
        installed: list[str] = []
        provider_error: str | None = None
        try:
            health = self.client.health()
            available = health.available
            version = health.version
            installed = [item.name for item in self.client.list_models()]
        except LLMError as exc:
            provider_error = str(exc)

        recommended = recommend_model(installed)
        recommendation_installed = recommended in installed
        config_written = False
        profile_status = "unavailable" if provider_error else "missing"
        profile_id: str | None = None
        profile_service = ModelProfileService(
            self.client,
            ModelProfileStore(build_layout(self.project_root).model_profiles),
        )
        if available and recommendation_installed:
            profile_status, profile_id = self._inspect_profile_status(profile_service, recommended)
        if not check_only and allow_config_write:
            InfiniteContextService(self.project_root).init()
            self._write_additive_config(recommended)
            config_written = True
            state = "existing"
            if available and recommendation_installed:
                profile, _created = profile_service.create_or_reuse(recommended)
                profile_id = profile.profile_id
                profile_status = (
                    "weak-unverified"
                    if profile.model_identity.identity_strength == IdentityStrength.WEAK
                    else profile.calibration_status.value
                )

        ready = available and recommendation_installed and (state == "existing" or check_only)
        if provider_error:
            message = provider_error
        elif not recommendation_installed:
            message = f"No preferred model is installed; install {recommended}, then run infctx setup again"
        elif check_only and state == "missing":
            message = "Checks passed; run infctx setup --yes to initialize repository state"
        elif not check_only and not allow_config_write:
            message = "Checks passed; re-run with --yes to write safe local configuration"
        else:
            message = "Setup is ready; run infctx chat"
        return SetupReport(
            project_root=str(self.project_root),
            state=state,
            ollama_available=available,
            ollama_version=version,
            installed_models=installed,
            recommended_model=recommended,
            recommendation_installed=recommendation_installed,
            config_written=config_written,
            profile_status=profile_status,
            profile_id=profile_id,
            ready=ready,
            message=message,
        )

    @staticmethod
    def _inspect_profile_status(profile_service: ModelProfileService, model_name: str) -> tuple[str, str | None]:
        identity, _details = profile_service.inspect_identity(model_name)
        if identity.identity_strength == IdentityStrength.WEAK:
            weak_profiles = profile_service.store.find_by_name("ollama", model_name)
            return "weak-unverified", weak_profiles[0].profile_id if weak_profiles else None
        try:
            profile = profile_service.store.find_exact("ollama", model_name, identity.model_digest)
        except ModelProfileDigestMismatchError:
            return "digest-mismatched", None
        except ModelProfileNotFoundError:
            return "missing", None
        if profile.calibration_status == CalibrationStatus.STALE:
            return "stale", profile.profile_id
        return profile.calibration_status.value, profile.profile_id

    def _write_additive_config(self, model: str) -> None:
        path = self.project_root / ".infctx" / "config.json"
        existing: dict[str, Any] = orjson.loads(path.read_bytes()) if path.exists() else {}
        llm = existing.setdefault("llm", {})
        if not isinstance(llm, dict):
            raise ValueError("Existing `llm` configuration must be an object; fix .infctx/config.json and retry")
        llm.setdefault("provider", "ollama")
        llm.setdefault("base_url", "http://localhost:11434")
        llm.setdefault("model", model)
        llm.setdefault("fallback_models", [FALLBACK_MODEL])
        chat = existing.setdefault("chat", {})
        if not isinstance(chat, dict):
            raise ValueError("Existing `chat` configuration must be an object; fix .infctx/config.json and retry")
        chat.setdefault("auto_snapshot", True)
        chat.setdefault("recent_turns", 6)
        chat.setdefault("stream", True)
        path.write_bytes(orjson.dumps(existing, option=orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE))


def recommend_model(installed: list[str]) -> str:
    if PRIMARY_MODEL in installed:
        return PRIMARY_MODEL
    if FALLBACK_MODEL in installed:
        return FALLBACK_MODEL
    return PRIMARY_MODEL


def discover_repository(start: Path) -> Path:
    current = start.resolve()
    if not current.exists() or not current.is_dir():
        raise ValueError(f"Repository path does not exist: {current}")
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() or (candidate / ".infctx").exists():
            return candidate
    raise ValueError(
        f"No Git or Infinite Context repository found from {current}; run this command inside a repository"
    )
