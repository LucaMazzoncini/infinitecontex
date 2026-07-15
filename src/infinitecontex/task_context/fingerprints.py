"""Canonical task-context semantic fingerprinting."""

from __future__ import annotations

import hashlib
from typing import Any

import orjson

from infinitecontex.task_context.models import TaskContextAnalysis


def fingerprint_payload(payload: Any) -> str:
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


def analysis_semantic_payload(analysis: TaskContextAnalysis) -> dict[str, Any]:
    return analysis.model_dump(
        mode="json",
        exclude={"analysis_id", "semantic_fingerprint", "created_at"},
    )


def compute_analysis_fingerprint(analysis: TaskContextAnalysis) -> str:
    return fingerprint_payload(analysis_semantic_payload(analysis))
