from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from infinitecontex.agent_runtime.evidence import ModelCallEvidence
from infinitecontex.agent_runtime.store import AgentRunStore
from infinitecontex.llm.models import ChatMessage, InstalledModel, ModelDetails, OllamaSampling
from infinitecontex.llm.ollama import OllamaClient
from infinitecontex.model_profiles.errors import ModelProfileDigestMismatchError
from infinitecontex.model_profiles.service import ModelProfileService
from infinitecontex.model_profiles.store import ModelProfileStore

NOW = datetime(2026, 7, 19, tzinfo=UTC)
DIGEST = "0" * 64


class ExactClient:
    def __init__(self, digest: str = DIGEST) -> None:
        self.digest = digest
        self.generated = False

    def list_models(self) -> list[InstalledModel]:
        return [InstalledModel(name="qwen3.6:35b", digest=self.digest)]

    def show_model(self, name: str) -> ModelDetails:
        return ModelDetails(name=name, parameters="num_ctx 32768", model_info={"qwen.context_length": 32768})

    def stream_chat(self, model: str, messages: object) -> object:
        self.generated = True
        return ()


def test_exact_profile_creation_and_non_generating_verification(tmp_path: Path) -> None:
    client = ExactClient()
    service = ModelProfileService(client, ModelProfileStore(tmp_path), clock=lambda: NOW)  # type: ignore[arg-type]
    profile, created = service.create_exact("qwen3.6:35b", DIGEST)
    assert created and profile.model_identity.model_digest == DIGEST
    verified = service.verify_exact(profile.profile_id)
    assert verified["verified"] is True
    assert verified["generation_invoked"] is False
    assert client.generated is False


def test_exact_profile_rejects_digest_or_alias_change(tmp_path: Path) -> None:
    service = ModelProfileService(ExactClient("1" * 64), ModelProfileStore(tmp_path), clock=lambda: NOW)  # type: ignore[arg-type]
    with pytest.raises(ModelProfileDigestMismatchError):
        service.create_exact("qwen3.6:35b", DIGEST)


class Response(io.BytesIO):
    def __iter__(self) -> "Response":
        return self

    def __next__(self) -> bytes:
        value = self.readline()
        if not value:
            raise StopIteration
        return value


def test_sampling_effective_values_are_transmitted(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, Any]] = []

    def opened(request: Any, timeout: float) -> Response:
        bodies.append(json.loads(request.data))
        return Response(b'{"message":{"content":"{}"},"done":true}\n')

    monkeypatch.setattr("infinitecontex.llm.ollama.urlopen", opened)
    sampling = OllamaSampling(num_predict=4096, num_ctx=32768, stop=("END",))
    list(OllamaClient().stream_chat("qwen3.6:35b", [ChatMessage(role="system", content="x")], sampling))
    assert bodies[0]["options"] == sampling.transmitted()
    assert bodies[0]["options"]["num_predict"] == 4096
    assert bodies[0]["options"]["stop"] == ["END"]


def test_model_call_evidence_is_append_only_and_contains_no_source(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path)
    value = ModelCallEvidence(
        call_id="agent-call-" + "a" * 24,
        evidence_fingerprint="b" * 64,
        run_id="run",
        step_number=1,
        session_id="session",
        task_id="task",
        grant_id="grant",
        profile_id="profile",
        model_name="qwen3.6:35b",
        model_digest=DIGEST,
        manifest_id="context-manifest-" + "c" * 24,
        manifest_fingerprint="d" * 64,
        admission_id="context-admission-" + "e" * 24,
        admission_decision="admitted",
        final_prompt_hash="f" * 64,
        request_envelope_hash="1" * 64,
        input_bytes=10,
        estimated_input_tokens=3,
        reserved_output_tokens=4096,
        operational_context_tokens=32768,
        remaining_tokens=20000,
        requested_sampling={"temperature": 0.1},
        effective_sampling={"temperature": 0.1},
        transmitted_sampling={"temperature": 0.1},
        created_at=NOW,
    )
    store.save_evidence("plan", "run", value)
    store.save_evidence("plan", "run", value)
    assert store.list_evidence("plan", "run") == (value,)
    assert b"repository source" not in store.serialize(value)
    with pytest.raises(ValueError, match="immutable"):
        store.save_evidence("plan", "run", value.model_copy(update={"input_bytes": 11}))
