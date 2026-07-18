"""Stable cryptographic identities for G2 invocation and execution records."""

from typing import Any

from infinitecontex.tools.execution_models import ReadOnlyExecutionGrant, ToolExecutionRecord, ToolInvocation
from infinitecontex.tools.fingerprints import sha256_payload


def invocation_fingerprint(value: ToolInvocation | dict[str, Any]) -> str:
    payload = value if isinstance(value, dict) else value.model_dump(mode="json")
    return sha256_payload(
        {
            key: item
            for key, item in payload.items()
            if key not in {"invocation_id", "invocation_fingerprint", "created_at"}
        }
    )


def grant_fingerprint(value: ReadOnlyExecutionGrant | dict[str, Any]) -> str:
    payload = value if isinstance(value, dict) else value.model_dump(mode="json")
    return sha256_payload(
        {key: item for key, item in payload.items() if key not in {"grant_id", "grant_fingerprint", "created_at"}}
    )


def record_fingerprint(value: ToolExecutionRecord | dict[str, Any]) -> str:
    payload = value if isinstance(value, dict) else value.model_dump(mode="json")
    return sha256_payload(
        {key: item for key, item in payload.items() if key not in {"execution_id", "record_fingerprint"}}
    )
