from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from infinitecontex.llm.models import ChatChunk, ChatMessage, InstalledModel, ModelDetails, ProviderHealth
from infinitecontex.model_profiles.service import ModelProfileService
from infinitecontex.model_profiles.store import ModelProfileStore
from infinitecontex.service import InfiniteContextService


class FakeClient:
    def health(self) -> ProviderHealth:
        return ProviderHealth(available=True, version="test")

    def list_models(self) -> list[InstalledModel]:
        return [InstalledModel(name="demo:latest", digest="sha256:demo")]

    def show_model(self, name: str) -> ModelDetails:
        return ModelDetails(name=name, parameters="num_ctx 16384")

    def stream_chat(self, model: str, messages: Sequence[ChatMessage]) -> Iterable[ChatChunk]:
        return []


def test_profile_persists_across_services_without_touching_existing_state(tmp_repo: Path) -> None:
    service = InfiniteContextService(tmp_repo)
    service.init()
    marker = tmp_repo / ".infctx" / "existing-marker.txt"
    marker.write_text("keep", encoding="utf-8")
    directory = service.layout.model_profiles

    profile, created = ModelProfileService(
        FakeClient(), ModelProfileStore(directory), clock=lambda: datetime(2026, 7, 14, tzinfo=UTC)
    ).create_or_reuse("demo:latest")
    loaded = ModelProfileStore(directory).find_exact("ollama", "demo:latest", "sha256:demo")

    assert created is True
    assert loaded == profile
    assert marker.read_text(encoding="utf-8") == "keep"
