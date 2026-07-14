"""Protocol implemented by local model providers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from infinitecontex.llm.models import ChatChunk, ChatMessage, InstalledModel, ModelDetails, ProviderHealth


class LLMClient(Protocol):
    def health(self) -> ProviderHealth: ...

    def list_models(self) -> list[InstalledModel]: ...

    def show_model(self, name: str) -> ModelDetails: ...

    def stream_chat(self, model: str, messages: Sequence[ChatMessage]) -> Iterable[ChatChunk]: ...
