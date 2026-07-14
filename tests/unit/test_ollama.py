from __future__ import annotations

import io
from typing import Any

import pytest

from infinitecontex.llm.errors import LLMConnectionError, LLMResponseError
from infinitecontex.llm.models import ChatMessage
from infinitecontex.llm.ollama import OllamaClient


class _Response(io.BytesIO):
    def __iter__(self) -> "_Response":
        return self

    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration
        return line


def test_ollama_health_models_details_and_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            _Response(b'{"version":"0.11.0"}'),
            _Response(b'{"models":[{"name":"qwen3.6:35b","size":24}]}'),
            _Response(
                b'{"digest":"abc","format":"gguf","parameters":"num_ctx 32768",'
                b'"capabilities":["completion","tools"],"details":{"family":"qwen"}}'
            ),
            _Response(
                b'{"message":{"content":"hel"},"done":false}\n{"message":{"content":"lo"},"done":true,"eval_count":2}\n'
            ),
        ]
    )
    requests: list[Any] = []

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        requests.append((request, timeout))
        return next(responses)

    monkeypatch.setattr("infinitecontex.llm.ollama.urlopen", fake_urlopen)
    client = OllamaClient(timeout=12)

    assert client.base_url == "http://localhost:11434"
    assert client.health().version == "0.11.0"
    assert client.list_models()[0].name == "qwen3.6:35b"
    details = client.show_model("qwen3.6:35b")
    assert details.capabilities == ["completion", "tools"]
    assert details.parameters == "num_ctx 32768"
    assert details.format == "gguf"
    chunks = list(client.stream_chat("qwen3.6:35b", [ChatMessage(role="user", content="hi")]))
    assert "".join(chunk.content for chunk in chunks) == "hello"
    assert chunks[-1].eval_count == 2
    assert len(requests) == 4


def test_ollama_maps_connection_and_invalid_stream_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.error import URLError

    def unavailable(_request: Any, timeout: float) -> _Response:
        raise URLError("offline")

    monkeypatch.setattr("infinitecontex.llm.ollama.urlopen", unavailable)
    with pytest.raises(LLMConnectionError, match="start Ollama"):
        OllamaClient().health()

    monkeypatch.setattr("infinitecontex.llm.ollama.urlopen", lambda _request, timeout: _Response(b"bad\n"))
    with pytest.raises(LLMResponseError, match="streaming JSON"):
        list(OllamaClient().stream_chat("model", []))
