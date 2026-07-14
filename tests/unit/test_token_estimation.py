from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import infinitecontex.cli as cli_module
from infinitecontex.cli import app
from infinitecontex.context_budget.estimation import (
    CONSERVATIVE_STRATEGY,
    LEGACY_STRATEGY,
    ConservativeTextEstimator,
    Utf8ByteUpperBoundEstimator,
    estimator_for_strategy,
)
from infinitecontex.context_budget.models import TokenCountProvenance

TESTS_ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = TESTS_ROOT / "fixtures" / "token_estimator" / "corpus.json"
GOLDEN_PATH = TESTS_ROOT / "golden" / "token_estimator_qwen2_5_coder.json"


def _fixtures() -> tuple[list[dict[str, Any]], dict[str, int]]:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["samples"]
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["counts"]
    return corpus, golden


def test_every_corpus_entry_is_a_deterministic_golden_upper_bound() -> None:
    corpus, golden = _fixtures()
    estimator = ConservativeTextEstimator()
    assert {sample["id"] for sample in corpus} == set(golden)
    for sample in corpus:
        first = estimator.estimate(sample["text"])
        assert first == estimator.estimate(sample["text"])
        assert first.token_count >= golden[sample["id"]], sample["id"]
        assert first.provenance == TokenCountProvenance.HEURISTIC
        assert first.strategy_name == CONSERVATIVE_STRATEGY


def test_corpus_efficiency_thresholds_and_common_improvement() -> None:
    corpus, golden = _fixtures()
    estimator = ConservativeTextEstimator()
    legacy = Utf8ByteUpperBoundEstimator()
    ordinary = [sample for sample in corpus if sample["category"] not in {"unicode_difficult", "edge"}]
    total_estimate = sum(estimator.estimate(sample["text"]).token_count for sample in corpus)
    total_reference = sum(golden.values())
    common_estimate = sum(estimator.estimate(sample["text"]).token_count for sample in ordinary)
    common_reference = sum(golden[sample["id"]] for sample in ordinary)
    common_legacy = sum(legacy.estimate(sample["text"]).token_count for sample in ordinary)
    assert total_estimate * 100 <= total_reference * 200
    assert common_estimate * 100 <= common_reference * 175
    assert common_estimate * 100 <= common_legacy * 70
    for sample in ordinary:
        reference = golden[sample["id"]]
        if reference:
            assert estimator.estimate(sample["text"]).token_count * 100 <= reference * 250, sample["id"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "    \t\n",
        "plain ASCII",
        "perché è già così",
        "确定性预算",
        "👩🏽‍💻🚀",
        "Cafe\u0301",
        "identifier_" + "segment" * 100,
        '{"compact":true,"values":[1,2,3]}',
        "prose then `source_code()` and more prose",
        "x" * 1_000_000,
    ],
    ids=[
        "empty",
        "whitespace",
        "ascii",
        "italian",
        "cjk",
        "emoji",
        "combining",
        "long-identifier",
        "minified-json",
        "mixed",
        "large",
    ],
)
def test_edge_inputs_are_deterministic_non_negative_and_estimated(text: str) -> None:
    result = ConservativeTextEstimator().estimate(text)
    assert result == ConservativeTextEstimator().estimate(text)
    assert result.token_count >= 0
    assert result.provenance == TokenCountProvenance.HEURISTIC


def test_line_endings_normalize_but_content_is_not_trimmed() -> None:
    estimator = ConservativeTextEstimator()
    assert estimator.estimate("one\r\ntwo\r") == estimator.estimate("one\ntwo\n")
    assert estimator.estimate(" one ").token_count > estimator.estimate("one").token_count


def test_isolated_surrogates_fail_safe_without_encoding_errors() -> None:
    estimate = ConservativeTextEstimator().estimate("before\ud800after")
    assert estimate.token_count > 0
    assert estimate.normalized_utf8_bytes == len("before\ud800after".encode("utf-8", errors="surrogatepass"))


def test_legacy_strategy_is_unchanged_and_resolvable() -> None:
    legacy = estimator_for_strategy(LEGACY_STRATEGY)
    assert isinstance(legacy, Utf8ByteUpperBoundEstimator)
    assert legacy.estimate("caffè\r\n").token_count == len("caffè\n".encode())
    assert estimator_for_strategy(CONSERVATIVE_STRATEGY).strategy_name == CONSERVATIVE_STRATEGY
    with pytest.raises(ValueError, match="Unsupported"):
        estimator_for_strategy("unknown-v99")


def test_estimate_text_cli_human_json_and_no_ollama(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class ForbiddenOllama:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("raw estimation must not construct Ollama")

    monkeypatch.setattr(cli_module, "OllamaClient", ForbiddenOllama)
    runner = CliRunner()
    human = runner.invoke(app, ["model", "budget", "estimate-text", "--text", "hello world"])
    assert human.exit_code == 0
    assert "Conservative Token Estimate" in human.stdout
    assert CONSERVATIVE_STRATEGY in human.stdout
    assert "False" in human.stdout

    source = tmp_path / "request.cs"
    source.write_text("public sealed class Demo {}\n", encoding="utf-8")
    structured = runner.invoke(
        app,
        ["model", "budget", "estimate-text", "--text-file", str(source), "--json"],
    )
    assert structured.exit_code == 0
    payload = json.loads(structured.stdout)
    assert payload["strategy"] == CONSERVATIVE_STRATEGY
    assert payload["provenance"] == "heuristic"
    assert payload["measured"] is False
    assert payload["exact"] is False


def test_estimate_text_cli_rejects_oversize_without_silent_truncation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "ESTIMATE_TEXT_FILE_LIMIT_BYTES", 4)
    source = tmp_path / "large.txt"
    source.write_text("12345", encoding="utf-8")
    runner = CliRunner()
    rejected = runner.invoke(app, ["model", "budget", "estimate-text", "--text-file", str(source)])
    assert rejected.exit_code == 2
    assert "--allow-large-file" in rejected.stdout
    allowed = runner.invoke(
        app,
        ["model", "budget", "estimate-text", "--text-file", str(source), "--allow-large-file"],
    )
    assert allowed.exit_code == 0
