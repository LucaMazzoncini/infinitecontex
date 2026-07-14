from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import orjson
import pytest
from pydantic import ValidationError

from infinitecontex.llm.models import ChatChunk, ChatMessage, InstalledModel, ModelDetails, ProviderHealth
from infinitecontex.model_profiles.errors import (
    ModelProfileDigestMismatchError,
    ModelProfileFormatError,
    ModelProfileNotFoundError,
)
from infinitecontex.model_profiles.models import CalibrationStatus, IdentityStrength, ModelProfile
from infinitecontex.model_profiles.service import ModelProfileService
from infinitecontex.model_profiles.store import ModelProfileStore

NOW = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)


class FakeClient:
    def __init__(self, *, digest: str = "sha256:abc", parameters: str = "PARAMETER num_ctx 65536") -> None:
        self.digest = digest
        self.parameters = parameters
        self.show_calls = 0

    def health(self) -> ProviderHealth:
        return ProviderHealth(available=True, version="test")

    def list_models(self) -> list[InstalledModel]:
        return [
            InstalledModel(
                name="Qwen3.6:35B",
                digest=self.digest,
                size=24,
                modified_at="2026-07-14T00:00:00Z",
                details={"family": "qwen", "parameter_size": "35B", "quantization_level": "Q4_K_M"},
            )
        ]

    def show_model(self, name: str) -> ModelDetails:
        self.show_calls += 1
        return ModelDetails(
            name=name,
            parameters=self.parameters,
            format="gguf",
            capabilities=["tools", "completion"],
            model_info={"qwen.context_length": 262144, "qwen.block_count": 64, "unused.large": "drop"},
        )

    def stream_chat(self, model: str, messages: Sequence[ChatMessage]) -> Iterable[ChatChunk]:
        return []


def _create(store: ModelProfileStore, client: FakeClient | None = None) -> ModelProfile:
    profile, created = ModelProfileService(client or FakeClient(), store, clock=lambda: NOW).create_or_reuse(
        "qwen3.6:35b"
    )
    assert created is True
    return profile


def test_conservative_profile_separates_context_capacities_and_is_uncalibrated(tmp_path: Path) -> None:
    profile = _create(ModelProfileStore(tmp_path))
    assert profile.model_identity.model_name == "Qwen3.6:35B"
    assert profile.model_identity.normalized_model_name == "qwen3.6:35b"
    assert profile.model_identity.identity_strength == IdentityStrength.VERIFIED
    assert profile.advertised_context_tokens == 262144
    assert profile.configured_context_tokens == 65536
    assert profile.operational_context_tokens == 32768
    assert profile.maximum_recommended_input_tokens == 20480
    assert profile.calibration_status == CalibrationStatus.UNCALIBRATED
    assert profile.calibrated_at is None
    assert profile.calibration_evidence == []
    assert profile.token_estimation_strategy == "conservative-mixed-text-v2"
    assert "unused.large" not in profile.model_identity.metadata["model_info"]


def test_missing_configured_context_uses_smaller_estimated_default(tmp_path: Path) -> None:
    profile = _create(ModelProfileStore(tmp_path), FakeClient(parameters=""))
    assert profile.configured_context_tokens == 8192
    assert profile.operational_context_tokens == 8192
    assert profile.provenance["configured_context_tokens"] == "estimated"


def test_weak_identity_never_fabricates_digest(tmp_path: Path) -> None:
    store = ModelProfileStore(tmp_path)
    profile = _create(store, FakeClient(digest=""))
    assert profile.model_identity.model_digest is None
    assert profile.model_identity.identity_strength == IdentityStrength.WEAK
    with pytest.raises(ModelProfileNotFoundError, match="without a model digest"):
        store.find_exact("ollama", "qwen3.6:35b", None)


def test_deterministic_serialization_and_idempotent_reuse(tmp_path: Path) -> None:
    store = ModelProfileStore(tmp_path)
    client = FakeClient()
    profile = _create(store, client)
    assert store.serialize(profile) == store.serialize(profile)
    persisted = next(tmp_path.glob("*.json")).read_bytes()
    assert persisted == store.serialize(profile)
    reused, created = ModelProfileService(client, store, clock=lambda: NOW).create_or_reuse("QWEN3.6:35B")
    assert created is False
    assert reused.profile_id == profile.profile_id
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_legacy_estimator_strategy_profile_remains_readable(tmp_path: Path) -> None:
    store = ModelProfileStore(tmp_path)
    legacy = _create(store).model_copy(update={"token_estimation_strategy": "normalized-utf8-byte-upper-bound-v1"})
    store.save(legacy)
    loaded = store.find_exact("ollama", "qwen3.6:35b", "sha256:abc")
    assert loaded.token_estimation_strategy == "normalized-utf8-byte-upper-bound-v1"


def test_atomic_write_uses_same_directory_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ModelProfileStore(tmp_path)
    profile = ModelProfileService(FakeClient(), store, clock=lambda: NOW)._conservative_profile(  # noqa: SLF001
        *ModelProfileService(FakeClient(), store, clock=lambda: NOW).inspect_identity("qwen3.6:35b")
    )
    observed: list[tuple[Path, Path]] = []
    real_replace = __import__("os").replace

    def capture_replace(source: str | Path, target: str | Path) -> None:
        source_path, target_path = Path(source), Path(target)
        assert source_path.parent == target_path.parent == tmp_path
        assert source_path.exists()
        observed.append((source_path, target_path))
        real_replace(source, target)

    monkeypatch.setattr("infinitecontex.model_profiles.store.os.replace", capture_replace)
    target = store.save(profile)
    assert observed and target.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_malformed_and_future_schema_files_fail_safely(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(ModelProfileFormatError, match="malformed"):
        ModelProfileStore(tmp_path).list_profiles()

    (tmp_path / "broken.json").write_bytes(orjson.dumps({"schema_version": 2}))
    with pytest.raises(ModelProfileFormatError, match="Unsupported"):
        ModelProfileStore(tmp_path).list_profiles()


def test_schema_rejects_unsafe_budget_and_invalid_calibration(tmp_path: Path) -> None:
    profile = _create(ModelProfileStore(tmp_path))
    payload = profile.model_dump(mode="json")
    payload["operational_context_tokens"] = payload["configured_context_tokens"] + 1
    with pytest.raises(ValidationError, match="cannot exceed"):
        ModelProfile.model_validate(payload)

    payload = profile.model_dump(mode="json")
    payload["calibration_status"] = "calibrated"
    with pytest.raises(ValidationError, match="require a calibration"):
        ModelProfile.model_validate(payload)


def test_exact_digest_lookup_refuses_silent_mismatch(tmp_path: Path) -> None:
    store = ModelProfileStore(tmp_path)
    _create(store)
    assert store.find_exact("OLLAMA", "qwen3.6:35b", "sha256:abc")
    with pytest.raises(ModelProfileDigestMismatchError, match="not digest"):
        store.find_exact("ollama", "qwen3.6:35b", "sha256:new")
    with pytest.raises(ModelProfileNotFoundError):
        store.find_exact("ollama", "other:latest", "sha256:abc")
