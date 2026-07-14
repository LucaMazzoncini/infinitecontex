from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from infinitecontex.chat.application import ChatApplication
from infinitecontex.chat.history import ConversationHistory
from infinitecontex.context_admission.dispatch import GatedChatDispatcher
from infinitecontex.context_admission.gate import ContextAdmissionGate
from infinitecontex.context_admission.store import AdmissionRecordStore
from infinitecontex.context_budget.calculator import ContextBudgetCalculator
from infinitecontex.context_packing.service import ContextPackingService
from infinitecontex.context_packing.store import ContextManifestStore
from infinitecontex.llm.models import ChatChunk, ChatMessage, InstalledModel, ModelDetails, ProviderHealth
from infinitecontex.model_profiles.models import (
    CalibrationStatus,
    IdentityStrength,
    ModelIdentity,
    ModelProfile,
    ValueProvenance,
)
from infinitecontex.model_profiles.store import ModelProfileStore


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


def chat_app(tmp_path: Path, fake: FakeClient) -> ChatApplication:
    now = datetime(2026, 7, 15, tzinfo=UTC)
    keys = (
        "advertised_context_tokens",
        "configured_context_tokens",
        "operational_context_tokens",
        "reserved_output_tokens",
        "reserved_tool_result_tokens",
        "reserved_system_prompt_tokens",
        "safety_margin_tokens",
        "maximum_recommended_input_tokens",
        "tokenizer_strategy",
        "token_estimation_strategy",
    )
    profile = ModelProfile(
        profile_id="chat-profile",
        model_identity=ModelIdentity(
            provider="ollama",
            model_name="fake",
            normalized_model_name="fake",
            model_digest="sha256:fake",
            identity_strength=IdentityStrength.VERIFIED,
            inspected_at=now,
        ),
        advertised_context_tokens=4096,
        configured_context_tokens=4096,
        operational_context_tokens=4096,
        reserved_output_tokens=256,
        reserved_tool_result_tokens=128,
        reserved_system_prompt_tokens=128,
        safety_margin_tokens=128,
        maximum_recommended_input_tokens=3456,
        tokenizer_strategy="unverified",
        token_estimation_strategy="conservative-mixed-text-v2",
        calibration_status=CalibrationStatus.UNCALIBRATED,
        created_at=now,
        updated_at=now,
        provenance={key: ValueProvenance.ESTIMATED for key in keys},
    )
    profiles = ModelProfileStore(tmp_path / "profiles")
    profiles.save(profile)
    manifests = ContextManifestStore(tmp_path / "manifests")
    packing = ContextPackingService(profiles, ContextBudgetCalculator(), manifest_store=manifests)
    gate = ContextAdmissionGate(profiles, manifests, AdmissionRecordStore(tmp_path / "admissions"))
    return ChatApplication(
        tmp_path, GatedChatDispatcher(gate, fake), packing, profile, "fake", max_turns=2, auto_snapshot=False
    )


def test_history_is_bounded_by_complete_turns() -> None:
    history = ConversationHistory(2)
    for number in range(3):
        history.add_turn(f"u{number}", f"a{number}")
    assert [message.content for message in history.messages()] == ["u1", "a1", "u2", "a2"]


def test_chat_streams_and_contains_slash_commands(tmp_path: Path) -> None:
    fake = FakeClient()
    app = chat_app(tmp_path, fake)
    output: list[str] = []

    assert app.handle("hello", output.append) is True
    assert "".join(output) == "one two"
    assert fake.requests[-1][-1].content == "hello"
    output.clear()
    assert app.handle("/unknown", output.append) is True
    assert "Unknown command" in output[0]
    assert len(fake.requests) == 1
    assert app.handle("/quit", output.append) is False


def test_chat_context_reports_hard_runtime_gate(tmp_path: Path) -> None:
    app = chat_app(tmp_path, FakeClient())
    output: list[str] = []
    app.handle("/context", output.append)
    assert "Hard runtime gating: active" in output[0]
