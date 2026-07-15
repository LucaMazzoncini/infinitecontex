"""Canonical identities for tool definitions, registries, and policy decisions."""

from __future__ import annotations

import hashlib
from typing import Any

import orjson

from infinitecontex.tools.models import ToolDefinition, ToolPolicyDecision


def sha256_payload(payload: Any) -> str:
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


def generate_tool_id(canonical_name: str, tool_version: str) -> str:
    return f"tool-{sha256_payload({'canonical_name': canonical_name.casefold(), 'version': tool_version})[:24]}"


def definition_payload(definition: ToolDefinition) -> dict[str, Any]:
    return definition.model_dump(
        mode="json",
        exclude={"tool_id", "definition_fingerprint", "created_at", "updated_at"},
    )


def definition_fingerprint(definition: ToolDefinition) -> str:
    return sha256_payload(definition_payload(definition))


def decision_payload(decision: ToolPolicyDecision) -> dict[str, Any]:
    return decision.model_dump(
        mode="json",
        exclude={"decision_id", "semantic_fingerprint", "created_at"},
    )


def decision_fingerprint(decision: ToolPolicyDecision) -> str:
    return sha256_payload(decision_payload(decision))
