"""Model inspection and conservative uncalibrated profile creation."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from infinitecontex.llm.base import LLMClient
from infinitecontex.llm.models import InstalledModel, ModelDetails
from infinitecontex.model_profiles.errors import ModelProfileDigestMismatchError, ModelProfileNotFoundError
from infinitecontex.model_profiles.models import (
    CalibrationStatus,
    IdentityStrength,
    ModelIdentity,
    ModelProfile,
    ValueProvenance,
    normalize_model_name,
)
from infinitecontex.model_profiles.store import ModelProfileStore

DEFAULT_UNVERIFIED_CONTEXT_TOKENS = 8192
INITIAL_OPERATIONAL_LIMIT_TOKENS = 32768


class ModelProfileService:
    def __init__(
        self,
        client: LLMClient,
        store: ModelProfileStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.clock = clock or (lambda: datetime.now(UTC))

    def inspect_identity(self, model_name: str, provider: str = "ollama") -> tuple[ModelIdentity, ModelDetails]:
        normalized = normalize_model_name(model_name)
        installed = self.client.list_models()
        selected = next((item for item in installed if normalize_model_name(item.name) == normalized), None)
        if selected is None:
            raise ModelProfileNotFoundError(f"Model {model_name} is not installed; install it explicitly and retry")
        details = self.client.show_model(selected.name)
        digest = _clean_digest(selected.digest) or _clean_digest(details.digest)
        detail_values = selected.details or details.details
        identity = ModelIdentity(
            provider=provider,
            model_name=selected.name,
            normalized_model_name=normalize_model_name(selected.name),
            model_digest=digest,
            identity_strength=IdentityStrength.VERIFIED if digest else IdentityStrength.WEAK,
            model_family=_optional_string(detail_values.get("family")),
            parameter_size=_optional_string(detail_values.get("parameter_size")),
            quantization_level=_optional_string(detail_values.get("quantization_level")),
            metadata=_useful_metadata(selected, details),
            inspected_at=self.clock(),
        )
        return identity, details

    def create_exact(
        self, model_name: str, expected_digest: str, provider: str = "ollama"
    ) -> tuple[ModelProfile, bool]:
        identity, _ = self.inspect_identity(model_name, provider)
        if identity.model_name != model_name:
            raise ModelProfileDigestMismatchError("Ollama alias substitution is not allowed for exact profile creation")
        if identity.model_digest != expected_digest:
            raise ModelProfileDigestMismatchError(
                f"Installed digest {identity.model_digest!r} does not match expected digest {expected_digest!r}"
            )
        return self.create_or_reuse(model_name, provider)

    def verify_exact(self, profile_id: str) -> dict[str, object]:
        profile = self.store.find_by_id(profile_id)
        identity, _ = self.inspect_identity(profile.model_identity.model_name, profile.model_identity.provider)
        if (
            identity.model_name != profile.model_identity.model_name
            or identity.model_digest != profile.model_identity.model_digest
        ):
            raise ModelProfileDigestMismatchError("Installed model identity differs from the exact persisted profile")
        if profile.model_identity.model_digest is None:
            raise ModelProfileDigestMismatchError("Exact verification requires a digest-bound profile")
        return {
            "verified": True,
            "profile_id": profile.profile_id,
            "provider": profile.model_identity.provider,
            "model_name": profile.model_identity.model_name,
            "model_digest": profile.model_identity.model_digest,
            "profile_fingerprint": hashlib.sha256(self.store.serialize(profile)).hexdigest(),
            "operational_context_tokens": profile.operational_context_tokens,
            "reserved_output_tokens": profile.reserved_output_tokens,
            "sampling_contract": "ollama-options-v1",
            "generation_invoked": False,
        }

    def create_or_reuse(self, model_name: str, provider: str = "ollama") -> tuple[ModelProfile, bool]:
        identity, details = self.inspect_identity(model_name, provider)
        if identity.identity_strength == IdentityStrength.WEAK:
            profile = self._conservative_profile(identity, details)
            self.store.save(profile)
            return profile, True
        try:
            return (
                self.store.find_exact(provider, identity.normalized_model_name, identity.model_digest),
                False,
            )
        except (ModelProfileNotFoundError, ModelProfileDigestMismatchError):
            profile = self._conservative_profile(identity, details)
            self.store.save(profile)
            return profile, True

    def _conservative_profile(self, identity: ModelIdentity, details: ModelDetails) -> ModelProfile:
        now = self.clock()
        advertised = _advertised_context(details.model_info)
        configured = _configured_context(details.parameters)
        configured_source = ValueProvenance.DETECTED
        if configured is None:
            configured = (
                min(advertised, DEFAULT_UNVERIFIED_CONTEXT_TOKENS) if advertised else DEFAULT_UNVERIFIED_CONTEXT_TOKENS
            )
            configured_source = ValueProvenance.ESTIMATED
        elif advertised is not None and configured > advertised:
            configured = advertised
            configured_source = ValueProvenance.ESTIMATED
        operational = min(configured, INITIAL_OPERATIONAL_LIMIT_TOKENS)
        output, tool, system, safety = _conservative_reserves(operational)
        maximum_input = operational - output - tool - system - safety
        identity_key = identity.model_digest or "weak-unverified"
        raw_id = f"{identity.provider.casefold()}\0{identity.normalized_model_name}\0{identity_key}\0v1"
        profile_id = f"model-profile-{hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:20]}"
        return ModelProfile(
            profile_id=profile_id,
            model_identity=identity,
            advertised_context_tokens=advertised,
            configured_context_tokens=configured,
            operational_context_tokens=operational,
            reserved_output_tokens=output,
            reserved_tool_result_tokens=tool,
            reserved_system_prompt_tokens=system,
            safety_margin_tokens=safety,
            maximum_recommended_input_tokens=maximum_input,
            tokenizer_strategy="provider-tokenizer-unverified",
            token_estimation_strategy="conservative-mixed-text-v2",
            calibration_status=CalibrationStatus.UNCALIBRATED,
            calibrated_at=None,
            calibration_evidence=[],
            hardware_profile_ref=None,
            created_at=now,
            updated_at=now,
            provenance={
                "advertised_context_tokens": (
                    ValueProvenance.DETECTED if advertised is not None else ValueProvenance.ESTIMATED
                ),
                "configured_context_tokens": configured_source,
                "operational_context_tokens": ValueProvenance.ESTIMATED,
                "reserved_output_tokens": ValueProvenance.ESTIMATED,
                "reserved_tool_result_tokens": ValueProvenance.ESTIMATED,
                "reserved_system_prompt_tokens": ValueProvenance.ESTIMATED,
                "safety_margin_tokens": ValueProvenance.ESTIMATED,
                "maximum_recommended_input_tokens": ValueProvenance.ESTIMATED,
                "tokenizer_strategy": ValueProvenance.ESTIMATED,
                "token_estimation_strategy": ValueProvenance.ESTIMATED,
            },
        )


def _clean_digest(value: str) -> str | None:
    cleaned = value.strip()
    return cleaned or None


def _optional_string(value: object) -> str | None:
    return str(value) if value not in {None, ""} else None


def _configured_context(parameters: str) -> int | None:
    for line in parameters.splitlines():
        match = re.match(r"^\s*(?:PARAMETER\s+)?num_ctx\s+([0-9]+)\s*$", line, flags=re.IGNORECASE)
        if match:
            value = int(match.group(1))
            return value if value > 0 else None
    return None


def _advertised_context(model_info: dict[str, Any]) -> int | None:
    suffixes = (".context_length", ".max_position_embeddings")
    values = [
        value
        for key, value in model_info.items()
        if key.casefold().endswith(suffixes) and isinstance(value, int) and value > 0
    ]
    return max(values) if values else None


def _conservative_reserves(operational: int) -> tuple[int, int, int, int]:
    if operational >= 32768:
        return 4096, 2048, 2048, 4096
    if operational >= 16384:
        return 2048, 1024, 1024, 2048
    return (
        max(512, operational // 8),
        max(256, operational // 16),
        max(256, operational // 16),
        max(512, operational // 8),
    )


def _useful_metadata(installed: InstalledModel, details: ModelDetails) -> dict[str, Any]:
    scalar_info = {
        key: value
        for key, value in sorted(details.model_info.items())
        if isinstance(value, (str, int, float, bool))
        and (
            key.casefold().endswith((".context_length", ".max_position_embeddings"))
            or any(fragment in key.casefold() for fragment in ("architecture", "parameter_count", "block_count"))
        )
    }
    return {
        "capabilities": sorted(details.capabilities),
        "format": details.format,
        "installed_size_bytes": installed.size,
        "modified_at": installed.modified_at,
        "model_info": scalar_info,
    }
