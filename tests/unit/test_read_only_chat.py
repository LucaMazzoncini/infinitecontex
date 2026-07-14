from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from infinitecontex.chat.application import ChatApplication
from infinitecontex.chat.history import ConversationHistory
from infinitecontex.llm.models import ChatChunk, ChatMessage, InstalledModel, ModelDetails, ProviderHealth


class FakeClient:
    def __init__(self) -> None:
        self.requests: list[list[ChatMessage]] = []

    def health(self) -> ProviderHealth:
        return ProviderHealth(available=True, version="test")

    def list_models(self) -> list[InstalledModel]:
        return [InstalledModel(name="fake")]

    def show_model(self, name: str) -> ModelDetails:
        return ModelDetails(name=name)

    def stream_chat(self, model: str, messages: Sequence[ChatMessage]) -> Iterable[ChatChunk]:
        self.requests.append(list(messages))
        yield ChatChunk(content="one ")
        yield ChatChunk(content="two", done=True)


def test_history_is_bounded_by_complete_turns() -> None:
    history = ConversationHistory(2)
    for number in range(3):
        history.add_turn(f"u{number}", f"a{number}")
    assert [message.content for message in history.messages()] == ["u1", "a1", "u2", "a2"]


def test_chat_streams_and_contains_slash_commands(tmp_path: Path) -> None:
    fake = FakeClient()
    app = ChatApplication(tmp_path, fake, "fake", max_turns=2, auto_snapshot=False)
    output: list[str] = []

    assert app.handle("hello", output.append) is True
    assert "".join(output) == "one two"
    assert fake.requests[-1][-1].content == "hello"
    output.clear()
    assert app.handle("/unknown", output.append) is True
    assert "Unknown command" in output[0]
    assert len(fake.requests) == 1
    assert app.handle("/quit", output.append) is False


def test_chat_context_truthfully_reports_first_slice(tmp_path: Path) -> None:
    app = ChatApplication(tmp_path, FakeClient(), "fake", auto_snapshot=False)
    output: list[str] = []
    app.handle("/context", output.append)
    assert "not active" in output[0]
