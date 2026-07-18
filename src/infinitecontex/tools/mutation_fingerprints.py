"""Deterministic G3 identities excluding timestamps and display-only preview."""

from __future__ import annotations

from pydantic import BaseModel

from infinitecontex.tools.fingerprints import sha256_payload


def _semantic(value: BaseModel, excluded: set[str]) -> str:
    payload = value.model_dump(mode="json", exclude=excluded)
    return sha256_payload(payload)


def proposal_fingerprint(value: BaseModel) -> str:
    return _semantic(value, {"proposal_id", "semantic_fingerprint", "created_at", "diff_preview"})


def approval_fingerprint(value: BaseModel) -> str:
    return _semantic(value, {"approval_id", "approval_fingerprint", "decided_at"})


def mutation_record_fingerprint(value: BaseModel) -> str:
    return _semantic(value, {"mutation_id", "record_fingerprint", "started_at", "completed_at"})


def authorization_fingerprint(value: BaseModel) -> str:
    return _semantic(value, {"authorization_id", "authorization_fingerprint"})
