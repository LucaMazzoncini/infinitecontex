"""Ollama adapter using its native localhost HTTP API."""

from __future__ import annotations

import json
import socket
from collections.abc import Iterable, Sequence
from typing import Any, BinaryIO, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from infinitecontex.llm.errors import LLMConnectionError, LLMModelError, LLMResponseError, LLMTimeoutError
from infinitecontex.llm.models import (
    ChatChunk,
    ChatMessage,
    InstalledModel,
    ModelDetails,
    OllamaSampling,
    ProviderHealth,
)


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434", timeout: float = 900.0) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ollama base URL must be an absolute HTTP URL")
        self.base_url = base_url.rstrip("/")
        self.api_url = self.base_url if self.base_url.endswith("/api") else f"{self.base_url}/api"
        self.timeout = timeout

    def health(self) -> ProviderHealth:
        payload = self._json_request("GET", "/version")
        version = payload.get("version")
        if not isinstance(version, str):
            raise LLMResponseError("Ollama version response did not contain a version")
        return ProviderHealth(available=True, version=version)

    def list_models(self) -> list[InstalledModel]:
        payload = self._json_request("GET", "/tags")
        raw_models = payload.get("models", [])
        if not isinstance(raw_models, list):
            raise LLMResponseError("Ollama model-list response was invalid")
        return [InstalledModel.model_validate(item) for item in raw_models]

    def show_model(self, name: str) -> ModelDetails:
        payload = self._json_request("POST", "/show", {"model": name})
        return ModelDetails(
            name=name,
            digest=str(payload.get("digest", "")),
            format=str(payload.get("format", "")),
            parameters=str(payload.get("parameters", "")),
            capabilities=[str(item) for item in payload.get("capabilities", [])],
            details=cast(dict[str, Any], payload.get("details", {})),
            model_info=cast(dict[str, Any], payload.get("model_info", {})),
        )

    def stream_chat(
        self, model: str, messages: Sequence[ChatMessage], sampling: OllamaSampling | None = None
    ) -> Iterable[ChatChunk]:
        body = {
            "model": model,
            "messages": [message.model_dump() for message in messages],
            "stream": True,
        }
        if sampling is not None:
            body["options"] = sampling.transmitted()
        response = self._open("POST", "/chat", body)
        try:
            for raw_line in response:
                if not raw_line.strip():
                    continue
                try:
                    payload = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise LLMResponseError("Ollama returned invalid streaming JSON") from exc
                if payload.get("error"):
                    raise LLMModelError(f"Ollama chat failed: {payload['error']}")
                message = payload.get("message", {})
                yield ChatChunk(
                    content=str(message.get("content", "")),
                    done=bool(payload.get("done", False)),
                    prompt_eval_count=payload.get("prompt_eval_count"),
                    eval_count=payload.get("eval_count"),
                )
        finally:
            response.close()

    def _json_request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._open(method, path, body)
        try:
            payload = json.load(response)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LLMResponseError("Ollama returned invalid JSON") from exc
        finally:
            response.close()
        if not isinstance(payload, dict):
            raise LLMResponseError("Ollama returned an unexpected response")
        return cast(dict[str, Any], payload)

    def _open(self, method: str, path: str, body: dict[str, Any] | None = None) -> BinaryIO:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            f"{self.api_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            return cast(BinaryIO, urlopen(request, timeout=self.timeout))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            error_type = LLMModelError if exc.code == 404 else LLMResponseError
            raise error_type(f"Ollama request failed ({exc.code}): {detail or exc.reason}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise LLMTimeoutError("Ollama request timed out; check the service and try again") from exc
        except URLError as exc:
            raise LLMConnectionError(f"Could not reach Ollama at {self.base_url}; start Ollama and try again") from exc
