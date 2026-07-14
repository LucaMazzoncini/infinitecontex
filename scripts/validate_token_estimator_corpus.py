"""Validate and report deterministic golden-corpus estimator statistics."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from infinitecontex.context_budget.estimation import ConservativeTextEstimator, Utf8ByteUpperBoundEstimator

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "token_estimator" / "corpus.json"
GOLDEN = ROOT / "tests" / "golden" / "token_estimator_qwen2_5_coder.json"
DIFFICULT = {"unicode_difficult"}


def main() -> None:
    corpus: dict[str, Any] = json.loads(CORPUS.read_text(encoding="utf-8"))
    golden: dict[str, Any] = json.loads(GOLDEN.read_text(encoding="utf-8"))
    counts: dict[str, int] = golden["counts"]
    estimator = ConservativeTextEstimator()
    legacy = Utf8ByteUpperBoundEstimator()
    category_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    total_estimate = total_reference = total_legacy = 0
    common_estimate = common_reference = common_legacy = 0
    worst_common_bp = worst_difficult_bp = 0

    for sample in corpus["samples"]:
        reference = counts[sample["id"]]
        estimate = estimator.estimate(sample["text"]).token_count
        legacy_count = legacy.estimate(sample["text"]).token_count
        if estimate < reference:
            raise SystemExit(f"upper-bound violation: {sample['id']} estimate={estimate} reference={reference}")
        total_estimate += estimate
        total_reference += reference
        total_legacy += legacy_count
        category_totals[sample["category"]][0] += estimate
        category_totals[sample["category"]][1] += reference
        ratio_bp = _ratio_bp(estimate, reference)
        if sample["category"] in DIFFICULT:
            worst_difficult_bp = max(worst_difficult_bp, ratio_bp)
        elif sample["category"] != "edge":
            common_estimate += estimate
            common_reference += reference
            common_legacy += legacy_count
            worst_common_bp = max(worst_common_bp, ratio_bp)

    aggregate_bp = _ratio_bp(total_estimate, total_reference)
    common_bp = _ratio_bp(common_estimate, common_reference)
    legacy_fraction_bp = _ratio_bp(common_estimate, common_legacy)
    checks = {
        "aggregate_ratio_bp<=20000": aggregate_bp <= 20000,
        "common_ratio_bp<=17500": common_bp <= 17500,
        "worst_common_ratio_bp<=25000": worst_common_bp <= 25000,
        "worst_difficult_ratio_bp<=60000": worst_difficult_bp <= 60000,
        "common_v2_fraction_of_legacy_bp<=7000": legacy_fraction_bp <= 7000,
    }
    print(f"strategy={estimator.strategy_name} samples={len(corpus['samples'])}")
    print(
        f"aggregate_ratio={aggregate_bp / 10000:.4f} common_ratio={common_bp / 10000:.4f} "
        f"worst_common={worst_common_bp / 10000:.4f} worst_difficult={worst_difficult_bp / 10000:.4f}"
    )
    print(
        f"legacy_ratio={_ratio_bp(total_legacy, total_reference) / 10000:.4f} "
        f"common_v2_fraction_of_legacy={legacy_fraction_bp / 10000:.4f}"
    )
    for category, (estimated, reference) in sorted(category_totals.items()):
        print(f"category={category} ratio={_ratio_bp(estimated, reference) / 10000:.4f}")
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("threshold violation: " + ", ".join(failed))
    print("golden_upper_bound=PASS efficiency_thresholds=PASS")


def _ratio_bp(numerator: int, denominator: int) -> int:
    if denominator == 0:
        return 0 if numerator == 0 else 10**12
    return (numerator * 10000 + denominator - 1) // denominator


if __name__ == "__main__":
    main()
