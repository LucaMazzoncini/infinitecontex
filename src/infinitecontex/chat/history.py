"""Bounded verbatim dialogue retained for the current process."""

from __future__ import annotations

from collections import deque

from infinitecontex.llm.models import ChatMessage


class ConversationHistory:
    def __init__(self, max_turns: int) -> None:
        self.max_turns = max_turns
        self._messages: deque[ChatMessage] = deque(maxlen=max_turns * 2)

    def add_turn(self, user: str, assistant: str) -> None:
        self._messages.append(ChatMessage(role="user", content=user))
        self._messages.append(ChatMessage(role="assistant", content=assistant))

    def messages(self) -> list[ChatMessage]:
        return list(self._messages)

    @property
    def turn_count(self) -> int:
        return len(self._messages) // 2
