"""Stable SHA-256 identities for split and approval artifacts."""

import hashlib
from typing import Any

import orjson

from infinitecontex.task_splitting.models import SplitApproval, SplitProposal


def sha256_payload(payload: Any) -> str:
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


def proposal_fingerprint(proposal: SplitProposal) -> str:
    payload = proposal.model_dump(mode="json", exclude={"proposal_id", "semantic_fingerprint", "created_at"})
    for child in payload["proposed_children"]:
        child["context_fit"].pop("created_at", None)
        child["context_fit"].pop("analysis_id", None)
    return sha256_payload(payload)


def approval_fingerprint(approval: SplitApproval) -> str:
    return sha256_payload(
        approval.model_dump(mode="json", exclude={"approval_id", "approval_fingerprint"})
    )
