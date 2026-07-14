from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

import orjson

from infinitecontex.llm.models import ChatChunk, ChatMessage, InstalledModel, ModelDetails, ProviderHealth
from infinitecontex.setup.service import FALLBACK_MODEL, PRIMARY_MODEL, SetupService, recommend_model


class FakeClient:
    def health(self) -> ProviderHealth:
        return ProviderHealth(available=True, version="test")

    def list_models(self) -> list[InstalledModel]:
        return [InstalledModel(name=FALLBACK_MODEL)]

    def show_model(self, name: str) -> ModelDetails:
        return ModelDetails(name=name)

    def stream_chat(self, model: str, messages: Sequence[ChatMessage]) -> Iterable[ChatChunk]:
        return []


def test_setup_check_only_does_not_write(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    report = SetupService(tmp_path, FakeClient()).run(check_only=True, allow_config_write=False)
    assert report.recommended_model == FALLBACK_MODEL
    assert not (tmp_path / ".infctx").exists()
    assert report.config_written is False


def test_setup_preserves_existing_configuration_and_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    config_path = tmp_path / ".infctx" / "config.json"
    config_path.parent.mkdir()
    config_path.write_bytes(orjson.dumps({"project_name": "kept", "llm": {"model": "custom"}, "custom": 7}))
    service = SetupService(tmp_path, FakeClient())

    first_report = service.run(check_only=False, allow_config_write=True)
    first = config_path.read_bytes()
    second_report = service.run(check_only=False, allow_config_write=True)
    payload = orjson.loads(config_path.read_bytes())

    assert config_path.read_bytes() == first
    assert payload["project_name"] == "kept"
    assert payload["custom"] == 7
    assert payload["llm"]["model"] == "custom"
    assert payload["llm"]["base_url"] == "http://localhost:11434"
    assert first_report.profile_status == "weak-unverified"
    assert second_report.profile_id == first_report.profile_id
    assert len(list((tmp_path / ".infctx" / "model-profiles").glob("*.json"))) == 1


def test_model_recommendation_order() -> None:
    assert recommend_model([FALLBACK_MODEL, PRIMARY_MODEL]) == PRIMARY_MODEL
    assert recommend_model([FALLBACK_MODEL]) == FALLBACK_MODEL
    assert recommend_model([]) == PRIMARY_MODEL


def test_setup_remains_usable_when_ollama_is_unavailable(tmp_path: Path) -> None:
    from infinitecontex.llm.errors import LLMConnectionError

    class UnavailableClient(FakeClient):
        def health(self) -> ProviderHealth:
            raise LLMConnectionError("offline; start Ollama and retry")

    (tmp_path / ".git").mkdir()
    marker = tmp_path / "marker.txt"
    marker.write_text("keep", encoding="utf-8")
    report = SetupService(tmp_path, UnavailableClient()).run(check_only=False, allow_config_write=True)
    assert report.ollama_available is False
    assert report.profile_status == "unavailable"
    assert report.config_written is True
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not list((tmp_path / ".infctx" / "model-profiles").glob("*.json"))


def test_setup_check_only_reports_digest_mismatch_without_writing(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from infinitecontex.model_profiles.service import ModelProfileService
    from infinitecontex.model_profiles.store import ModelProfileStore

    class DigestClient(FakeClient):
        def __init__(self, digest: str) -> None:
            self.digest = digest

        def list_models(self) -> list[InstalledModel]:
            return [InstalledModel(name=FALLBACK_MODEL, digest=self.digest)]

    (tmp_path / ".git").mkdir()
    profile_dir = tmp_path / ".infctx" / "model-profiles"
    ModelProfileService(
        DigestClient("sha256:old"),
        ModelProfileStore(profile_dir),
        clock=lambda: datetime(2026, 7, 14, tzinfo=UTC),
    ).create_or_reuse(FALLBACK_MODEL)
    before = {path.name: path.read_bytes() for path in profile_dir.glob("*.json")}

    report = SetupService(tmp_path, DigestClient("sha256:new")).run(check_only=True, allow_config_write=False)

    assert report.profile_status == "digest-mismatched"
    assert {path.name: path.read_bytes() for path in profile_dir.glob("*.json")} == before
