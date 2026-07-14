"""Versioned, deterministic token-estimation strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from infinitecontex.context_budget.models import TokenCountProvenance

LEGACY_STRATEGY = "normalized-utf8-byte-upper-bound-v1"
CONSERVATIVE_STRATEGY = "conservative-mixed-text-v2"
NORMALIZATION = "crlf-and-cr-to-lf;no-trimming"


@dataclass(frozen=True)
class TokenEstimate:
    """An inspectable token estimate; heuristic counts are never measured counts."""

    token_count: int
    provenance: TokenCountProvenance
    strategy_name: str = "unspecified-estimator"
    strategy_version: int = 1
    normalized_characters: int = 0
    normalized_utf8_bytes: int = 0
    normalization: str = "unspecified"
    content_class: str = "unspecified"
    conservatism: str = "corpus-validated-upper-bound"


class TokenEstimator(Protocol):
    @property
    def strategy_name(self) -> str: ...

    def estimate(self, text: str) -> TokenEstimate: ...


def normalize_text(text: str) -> str:
    """Normalize line endings without trimming or Unicode normalization."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _utf8_size(text: str) -> int:
    # surrogatepass makes estimation total for untrusted Python strings while
    # preserving ordinary UTF-8 sizing exactly.
    return len(text.encode("utf-8", errors="surrogatepass"))


class Utf8ByteUpperBoundEstimator:
    """The M2 E2 byte-count strategy, retained without reinterpretation."""

    @property
    def strategy_name(self) -> str:
        return LEGACY_STRATEGY

    def estimate(self, text: str) -> TokenEstimate:
        normalized = normalize_text(text)
        byte_count = _utf8_size(normalized)
        return TokenEstimate(
            token_count=byte_count,
            provenance=TokenCountProvenance.HEURISTIC,
            strategy_name=self.strategy_name,
            strategy_version=1,
            normalized_characters=len(normalized),
            normalized_utf8_bytes=byte_count,
            normalization=NORMALIZATION,
            content_class=_content_class(normalized),
            conservatism="hard-byte-upper-bound",
        )


class ConservativeTextEstimator:
    """Linear mixed-text upper estimate validated against the golden corpus.

    ASCII alphanumeric runs cost one token per eight characters; punctuation
    (including underscores) costs one each. Horizontal whitespace is charged per eight-character run chunk,
    and every line break is charged. Non-ASCII Latin/combining/CJK characters
    cost two, supplementary characters cost four, and isolated surrogates cost
    three. A non-empty section has two tokens of structural overhead.
    """

    @property
    def strategy_name(self) -> str:
        return CONSERVATIVE_STRATEGY

    def estimate(self, text: str) -> TokenEstimate:
        normalized = normalize_text(text)
        byte_count = _utf8_size(normalized)
        if not normalized:
            return TokenEstimate(
                0,
                TokenCountProvenance.HEURISTIC,
                self.strategy_name,
                2,
                0,
                0,
                NORMALIZATION,
                "empty",
            )

        ascii_run_tokens = punctuation = line_breaks = 0
        ascii_run = 0
        horizontal_whitespace_tokens = 0
        whitespace_run = 0
        non_ascii = supplementary = surrogates = 0

        def flush_whitespace() -> None:
            nonlocal horizontal_whitespace_tokens, whitespace_run
            if whitespace_run:
                horizontal_whitespace_tokens += (whitespace_run + 7) // 8
                whitespace_run = 0

        def flush_ascii_run() -> None:
            nonlocal ascii_run_tokens, ascii_run
            if ascii_run:
                ascii_run_tokens += (ascii_run + 7) // 8
                ascii_run = 0

        for char in normalized:
            codepoint = ord(char)
            if char == "\n":
                flush_ascii_run()
                flush_whitespace()
                line_breaks += 1
            elif codepoint < 128 and char.isspace():
                flush_ascii_run()
                whitespace_run += 1
            elif codepoint < 128:
                flush_whitespace()
                if char.isalnum():
                    ascii_run += 1
                else:
                    flush_ascii_run()
                    punctuation += 1
            else:
                flush_ascii_run()
                flush_whitespace()
                if 0xD800 <= codepoint <= 0xDFFF:
                    surrogates += 1
                elif codepoint > 0xFFFF:
                    supplementary += 1
                else:
                    non_ascii += 1
        flush_whitespace()
        flush_ascii_run()

        estimate = (
            ascii_run_tokens
            + punctuation
            + horizontal_whitespace_tokens
            + line_breaks
            + (non_ascii * 2)
            + (supplementary * 4)
            + (surrogates * 3)
            + 2
        )
        return TokenEstimate(
            token_count=estimate,
            provenance=TokenCountProvenance.HEURISTIC,
            strategy_name=self.strategy_name,
            strategy_version=2,
            normalized_characters=len(normalized),
            normalized_utf8_bytes=byte_count,
            normalization=NORMALIZATION,
            content_class=_content_class(normalized),
        )


def estimator_for_strategy(strategy_name: str) -> TokenEstimator:
    """Resolve a supported persisted strategy identifier explicitly."""
    if strategy_name == LEGACY_STRATEGY:
        return Utf8ByteUpperBoundEstimator()
    if strategy_name == CONSERVATIVE_STRATEGY:
        return ConservativeTextEstimator()
    raise ValueError(f"Unsupported token-estimation strategy: {strategy_name}")


def _content_class(text: str) -> str:
    if not text:
        return "empty"
    if text.isspace():
        return "whitespace"
    cjk = supplementary = non_ascii = punctuation = 0
    for char in text:
        codepoint = ord(char)
        if _is_cjk(codepoint):
            cjk += 1
        elif codepoint > 0xFFFF:
            supplementary += 1
        elif codepoint >= 128:
            non_ascii += 1
        elif not char.isalnum() and not char.isspace() and char != "_":
            punctuation += 1
    if supplementary:
        return "supplementary-unicode"
    if cjk:
        return "cjk-or-mixed"
    if non_ascii:
        return "latin-unicode-or-mixed"
    if punctuation * 5 >= len(text):
        return "punctuation-dense"
    return "ascii-text"


def _is_cjk(codepoint: int) -> bool:
    return 0x3400 <= codepoint <= 0x4DBF or 0x4E00 <= codepoint <= 0x9FFF or 0xF900 <= codepoint <= 0xFAFF
