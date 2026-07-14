"""Pluggable deterministic token estimation."""

from __future__ import annotations

from typing import NamedTuple, Protocol

from infinitecontex.context_budget.models import TokenCountProvenance


class TokenEstimate(NamedTuple):
    """Token count and its provenance."""

    token_count: int
    provenance: TokenCountProvenance


class TokenEstimator(Protocol):
    @property
    def strategy_name(self) -> str: ...

    def estimate(self, text: str) -> TokenEstimate: ...


class ConservativeTextEstimator:
    """Use normalized UTF-8 byte count as a deliberately conservative upper estimate."""

    @property
    def strategy_name(self) -> str:
        return "normalized-utf8-byte-upper-bound-v1"

    def estimate(self, text: str) -> TokenEstimate:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        byte_count = len(normalized.encode("utf-8"))
        return TokenEstimate(byte_count, TokenCountProvenance.HEURISTIC)
