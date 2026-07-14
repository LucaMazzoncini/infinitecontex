"""Application logic for the first read-only chat slice."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from infinitecontex.chat.history import ConversationHistory
from infinitecontex.llm.base import LLMClient
from infinitecontex.llm.models import ChatMessage
from infinitecontex.service import InfiniteContextService

SYSTEM_MESSAGE = (
    "You are a read-only coding assistant. Do not claim to have modified files or run tools. "
    "Answer from the conversation only and clearly state when repository evidence is not available."
)


class ChatApplication:
    def __init__(
        self,
        project_root: Path,
        client: LLMClient,
        model: str,
        *,
        max_turns: int = 6,
        auto_snapshot: bool = True,
    ) -> None:
        self.project_root = project_root.resolve()
        self.client = client
        self.model = model
        self.history = ConversationHistory(max_turns)
        self.auto_snapshot = auto_snapshot
        self.snapshot_id: str | None = None
        self.snapshot_error: str | None = None

    def start(self) -> None:
        if not self.auto_snapshot:
            return
        try:
            snapshot = InfiniteContextService(self.project_root).snapshot()
            self.snapshot_id = snapshot.id
        except Exception as exc:
            self.snapshot_error = str(exc)

    def handle(self, text: str, emit: Callable[[str], None]) -> bool:
        stripped = text.strip()
        if not stripped:
            return True
        if stripped.startswith("/"):
            return self._slash(stripped, emit)
        messages = [ChatMessage(role="system", content=SYSTEM_MESSAGE), *self.history.messages()]
        messages.append(ChatMessage(role="user", content=stripped))
        parts: list[str] = []
        for chunk in self.client.stream_chat(self.model, messages):
            if chunk.content:
                parts.append(chunk.content)
                emit(chunk.content)
        self.history.add_turn(stripped, "".join(parts))
        return True

    def _slash(self, command: str, emit: Callable[[str], None]) -> bool:
        if command == "/quit":
            return False
        if command == "/help":
            emit("/help  /status  /context  /quit")
        elif command == "/status":
            snapshot = self.snapshot_id or (
                f"unavailable ({self.snapshot_error})" if self.snapshot_error else "disabled"
            )
            emit(f"Repo: {self.project_root}\nModel: {self.model}\nMode: read-only\nSnapshot: {snapshot}")
        elif command == "/context":
            emit(
                f"Recent conversation: {self.history.turn_count}/{self.history.max_turns} turns. "
                "Deterministic token budgeting is scheduled for M2 and is not active in this slice."
            )
        else:
            emit(f"Unknown command: {command}. Type /help for available commands.")
        return True
