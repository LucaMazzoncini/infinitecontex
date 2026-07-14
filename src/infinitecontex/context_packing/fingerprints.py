"""Canonical context-manifest fingerprint construction."""

from __future__ import annotations

import hashlib
from typing import Any

import orjson

from infinitecontex.context_packing.models import ContextManifest, RankingPolicy


def compute_manifest_fingerprint(manifest: ContextManifest, policy: RankingPolicy | None = None) -> str:
    active_policy = policy or RankingPolicy()
    payload = manifest_fingerprint_payload(
        policy=active_policy,
        estimator_strategy=manifest.estimator_strategy,
        estimator_version=manifest.estimator_version,
        profile_id=manifest.model_profile_id,
        provider=manifest.model_identity.provider,
        normalized_model_name=manifest.model_identity.normalized_model_name,
        digest=manifest.model_identity.model_digest,
        operational_context_tokens=manifest.operational_context_tokens,
        maximum_recommended_input_tokens=manifest.maximum_recommended_input_tokens,
        caller_reserved_input_tokens=manifest.caller_reserved_input_tokens,
        available_pack_tokens=manifest.available_pack_tokens,
        safeguards=manifest.safeguards,
        included=[item.model_dump(mode="json") for item in manifest.included],
        excluded=[item.model_dump(mode="json") for item in manifest.excluded],
        decision=manifest.decision,
        token_deficit=manifest.token_deficit,
    )
    return _sha256(payload)


def manifest_fingerprint_payload(
    *,
    policy: RankingPolicy,
    estimator_strategy: str,
    estimator_version: int,
    profile_id: str,
    provider: str,
    normalized_model_name: str,
    digest: str | None,
    operational_context_tokens: int,
    maximum_recommended_input_tokens: int,
    caller_reserved_input_tokens: int,
    available_pack_tokens: int,
    safeguards: dict[str, Any],
    included: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    decision: str,
    token_deficit: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ranking_policy": policy.model_dump(mode="json"),
        "estimator_strategy": estimator_strategy,
        "estimator_version": estimator_version,
        "profile_id": profile_id,
        "model": {
            "provider": provider,
            "normalized_model_name": normalized_model_name,
            "digest": digest,
        },
        "operational_context_tokens": operational_context_tokens,
        "maximum_recommended_input_tokens": maximum_recommended_input_tokens,
        "caller_reserved_input_tokens": caller_reserved_input_tokens,
        "available_pack_tokens": available_pack_tokens,
        "safeguards": safeguards,
        "included": included,
        "excluded": excluded,
        "decision": decision,
        "token_deficit": token_deficit,
    }


def fingerprint_payload(payload: dict[str, Any]) -> str:
    return _sha256(payload)


def _sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()
