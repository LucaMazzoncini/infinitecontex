from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import orjson
from typer.testing import CliRunner

from infinitecontex.cli import app
from infinitecontex.context_admission.dispatch import GatedChatDispatcher
from infinitecontex.context_admission.errors import AdmissionRejectedError
from infinitecontex.context_admission.gate import ContextAdmissionGate
from infinitecontex.context_admission.models import AdmissionRequest, AdmissionSection, AdmissionSectionKind
from infinitecontex.context_admission.store import AdmissionRecordStore
from infinitecontex.context_budget.calculator import ContextBudgetCalculator
from infinitecontex.context_packing.models import CandidateCategory, ContextCandidate
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

NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


def _profile(maximum: int = 700) -> ModelProfile:
    operational = maximum + 300
    values = {
        key: ValueProvenance.ESTIMATED
        for key in (
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
    }
    return ModelProfile(
        profile_id="profile-test",
        model_identity=ModelIdentity(
            provider="ollama",
            model_name="demo:latest",
            normalized_model_name="demo:latest",
            model_digest="sha256:test",
            identity_strength=IdentityStrength.VERIFIED,
            inspected_at=NOW,
        ),
        advertised_context_tokens=100000,
        configured_context_tokens=operational,
        operational_context_tokens=operational,
        reserved_output_tokens=100,
        reserved_tool_result_tokens=100,
        reserved_system_prompt_tokens=50,
        safety_margin_tokens=50,
        maximum_recommended_input_tokens=maximum,
        tokenizer_strategy="unverified",
        token_estimation_strategy="conservative-mixed-text-v2",
        calibration_status=CalibrationStatus.UNCALIBRATED,
        created_at=NOW,
        updated_at=NOW,
        provenance=values,
    )


def _runtime(tmp_path: Path, maximum: int = 700) -> tuple[ContextAdmissionGate, object, ModelProfile]:
    profiles = ModelProfileStore(tmp_path / "profiles")
    profile = _profile(maximum)
    profiles.save(profile)
    manifests = ContextManifestStore(tmp_path / "manifests")
    packing = ContextPackingService(profiles, ContextBudgetCalculator(), manifest_store=manifests, clock=lambda: NOW)
    candidates = (
        ContextCandidate(
            candidate_id="system",
            category=CandidateCategory.SYSTEM_INSTRUCTIONS,
            label="system",
            content="be safe",
            mandatory=True,
        ),
        ContextCandidate(
            candidate_id="user",
            category=CandidateCategory.DIRECT_USER_REQUEST,
            label="user",
            content="hello",
            mandatory=True,
            direct_request_match=True,
        ),
    )
    manifest = packing.pack("demo:latest", candidates, digest="sha256:test", persist=True)
    gate = ContextAdmissionGate(profiles, manifests, AdmissionRecordStore(tmp_path / "admissions"))
    return gate, manifest, profile


def _request(manifest: object, profile: ModelProfile, **updates: object) -> AdmissionRequest:
    included = {item.candidate.candidate_id: item.candidate for item in manifest.included}  # type: ignore[attr-defined]
    values: dict[str, object] = {
        "provider": "ollama",
        "model_name": "demo:latest",
        "normalized_model_name": "demo:latest",
        "model_digest": "sha256:test",
        "profile_id": profile.profile_id,
        "manifest_id": manifest.manifest_id,  # type: ignore[attr-defined]
        "system_instructions": AdmissionSection(
            section_id="system",
            kind=AdmissionSectionKind.SYSTEM_INSTRUCTIONS,
            role="system",
            content="be safe",
            manifest_candidate_id="system",
            manifest_candidate_fingerprint=included["system"].candidate_fingerprint,
        ),
        "current_user_request": AdmissionSection(
            section_id="user",
            kind=AdmissionSectionKind.CURRENT_USER_REQUEST,
            role="user",
            content="hello",
            manifest_candidate_id="user",
            manifest_candidate_fingerprint=included["user"].candidate_fingerprint,
        ),
        "requested_output_tokens": 100,
        "requested_tool_result_tokens": 100,
        "calculated_at": NOW,
    }
    values.update(updates)
    return AdmissionRequest.model_validate(values)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Sequence[ChatMessage]]] = []

    def health(self) -> ProviderHealth:
        return ProviderHealth(available=True, version="test")

    def list_models(self) -> list[InstalledModel]:
        return []

    def show_model(self, name: str) -> ModelDetails:
        return ModelDetails(name=name)

    def stream_chat(self, model: str, messages: Sequence[ChatMessage]) -> Iterable[ChatChunk]:
        self.calls.append((model, messages))
        yield ChatChunk(content="ok", done=True)


def test_exact_request_admitted_repeated_fingerprint_and_compact_record(tmp_path: Path) -> None:
    gate, manifest, profile = _runtime(tmp_path)
    request = _request(manifest, profile)
    first = gate.evaluate(request)
    second = gate.evaluate(request)
    assert first.admitted and first.request_fingerprint == second.request_fingerprint
    assert first.actual_estimated_input_tokens > 0
    serialized = gate.record_store.serialize(gate.record_store.load(first.admission_id))
    assert b"be safe" not in serialized and b"hello" not in serialized


def test_changed_content_digest_and_allowance_rejected_without_dispatch(tmp_path: Path) -> None:
    gate, manifest, profile = _runtime(tmp_path)
    fake = FakeClient()
    changed = _request(
        manifest,
        profile,
        current_user_request=_request(manifest, profile).current_user_request.model_copy(update={"content": "changed"}),
    )
    try:
        list(GatedChatDispatcher(gate, fake).stream_chat(changed))
    except AdmissionRejectedError as exc:
        assert exc.result.decision == "rejected_content_mismatch"
    assert fake.calls == []
    mismatch = gate.evaluate(_request(manifest, profile, model_digest="sha256:wrong"))
    assert mismatch.decision == "rejected_profile_mismatch"
    overflow = gate.evaluate(_request(manifest, profile, requested_output_tokens=101))
    assert overflow.decision == "rejected_allowance_overflow"


def test_exact_payload_dispatched_once_and_record_updated(tmp_path: Path) -> None:
    gate, manifest, profile = _runtime(tmp_path)
    fake = FakeClient()
    chunks = list(GatedChatDispatcher(gate, fake).stream_chat(_request(manifest, profile)))
    assert [item.content for item in chunks] == ["ok"]
    assert len(fake.calls) == 1
    assert [item.content for item in fake.calls[0][1]] == ["be safe", "hello"]
    assert gate.record_store.list_records()[0].dispatch_state == "completed"


def test_tampered_manifest_and_one_token_budget_overflow_fail_closed(tmp_path: Path) -> None:
    gate, manifest, profile = _runtime(tmp_path, maximum=20)
    path = gate.manifest_store.directory / f"{manifest.manifest_id}.json"
    data = orjson.loads(path.read_bytes())
    data["included"][0]["reason"] = "tampered"
    path.write_bytes(orjson.dumps(data))
    result = gate.evaluate(_request(manifest, profile))
    assert not result.admitted
    assert result.decision == "rejected_invalid_manifest"

    overflow_gate, overflow_manifest, overflow_profile = _runtime(tmp_path / "overflow", maximum=10)
    overflow = overflow_gate.evaluate(_request(overflow_manifest, overflow_profile))
    assert overflow.decision == "rejected_budget_overflow"
    assert overflow.actual_estimated_input_tokens > overflow.maximum_recommended_input_tokens


def test_cli_human_json_and_admission_record_list_show_without_ollama(tmp_path: Path) -> None:
    gate, manifest, profile = _runtime(tmp_path / ".infctx")
    # Mirror the production layout while retaining the helper's isolated names.
    layout = tmp_path / ".infctx"
    (layout / "model-profiles").mkdir(parents=True)
    (layout / "context-manifests").mkdir(parents=True)
    for source in gate.profile_store.directory.glob("*.json"):
        (layout / "model-profiles" / source.name).write_bytes(source.read_bytes())
    for source in gate.manifest_store.directory.glob("*.json"):
        (layout / "context-manifests" / source.name).write_bytes(source.read_bytes())
    request_file = tmp_path / "request.json"
    request_file.write_bytes(orjson.dumps(_request(manifest, profile).model_dump(mode="json")))
    runner = CliRunner()
    human = runner.invoke(
        app, ["--project-root", str(tmp_path), "context", "admit", "--request-file", str(request_file)]
    )
    assert human.exit_code == 0 and "admitted" in human.stdout
    structured = runner.invoke(
        app, ["--project-root", str(tmp_path), "context", "admit", "--request-file", str(request_file), "--json"]
    )
    assert structured.exit_code == 0
    payload = orjson.loads(structured.stdout)
    admission_id = payload["admission_id"]
    listed = runner.invoke(app, ["--project-root", str(tmp_path), "context", "admission", "list", "--json"])
    shown = runner.invoke(
        app, ["--project-root", str(tmp_path), "context", "admission", "show", admission_id, "--json"]
    )
    assert admission_id in listed.stdout and admission_id in shown.stdout


def test_bounded_large_request_is_deterministic_and_fails_closed(tmp_path: Path) -> None:
    gate, manifest, profile = _runtime(tmp_path)
    sections = tuple(
        AdmissionSection(
            section_id=f"runtime-{index:04d}",
            kind=AdmissionSectionKind.ADDITIONAL_RUNTIME,
            role="user",
            content="x",
            mandatory=False,
        )
        for index in range(2000)
    )
    request = _request(manifest, profile, additional_runtime_sections=sections)
    first = gate.evaluate(request)
    second = gate.evaluate(request)
    assert first.decision == "rejected_budget_overflow"
    assert first.request_fingerprint == second.request_fingerprint
    assert len(first.sections) == 2003
