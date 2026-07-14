from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import orjson
import pytest
from pydantic import ValidationError

from infinitecontex.context_budget.calculator import ContextBudgetCalculator
from infinitecontex.context_budget.estimation import ConservativeTextEstimator
from infinitecontex.context_budget.models import RetentionPriority, TokenCountProvenance
from infinitecontex.context_packing.errors import ContextManifestFormatError, ContextManifestNotFoundError
from infinitecontex.context_packing.models import (
    CandidateCategory,
    ChangeState,
    ContextCandidate,
    ExclusionReason,
    ManifestDecision,
    RelevanceSignals,
)
from infinitecontex.context_packing.ranking import ContextCandidateRanker
from infinitecontex.context_packing.service import ContextPackingService
from infinitecontex.context_packing.store import ContextManifestStore
from infinitecontex.model_profiles.errors import ModelProfileDigestMismatchError, ModelProfileNotFoundError
from infinitecontex.model_profiles.models import (
    CalibrationStatus,
    IdentityStrength,
    ModelIdentity,
    ModelProfile,
    ValueProvenance,
)
from infinitecontex.model_profiles.store import ModelProfileStore

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def profile(
    *, maximum: int = 700, digest: str | None = "sha256:test", strategy: str = "conservative-mixed-text-v2"
) -> ModelProfile:
    operational = maximum + 300
    identity = ModelIdentity(
        provider="ollama",
        model_name="demo:latest",
        normalized_model_name="demo:latest",
        model_digest=digest,
        identity_strength=IdentityStrength.VERIFIED if digest else IdentityStrength.WEAK,
        inspected_at=NOW,
    )
    estimated = ValueProvenance.ESTIMATED
    return ModelProfile(
        profile_id=f"profile-{digest or 'weak'}-{maximum}",
        model_identity=identity,
        advertised_context_tokens=100_000,
        configured_context_tokens=operational,
        operational_context_tokens=operational,
        reserved_output_tokens=100,
        reserved_tool_result_tokens=100,
        reserved_system_prompt_tokens=50,
        safety_margin_tokens=50,
        maximum_recommended_input_tokens=maximum,
        tokenizer_strategy="unverified",
        token_estimation_strategy=strategy,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        created_at=NOW,
        updated_at=NOW,
        provenance={
            key: estimated
            for key in (
                "advertised_context_tokens",
                "configured_context_tokens",
                "operational_context_tokens",
                "reserved_output_tokens",
                "reserved_tool_result_tokens",
                "reserved_system_prompt_tokens",
                "safety_margin_tokens",
                "maximum_recommended_input_tokens",
                "tokenizer_strategy",
                "token_estimation_strategy",
            )
        },
    )


def candidate(
    candidate_id: str,
    tokens: int = 10,
    *,
    category: CandidateCategory = CandidateCategory.MISCELLANEOUS,
    mandatory: bool = False,
    direct: bool = False,
    priority: RetentionPriority = RetentionPriority.NORMAL,
    content: str | None = None,
    **updates: object,
) -> ContextCandidate:
    values: dict[str, object] = {
        "candidate_id": candidate_id,
        "category": category,
        "label": candidate_id,
        "content": content if content is not None else f"content-{candidate_id}",
        "token_count": tokens,
        "mandatory": mandatory,
        "direct_request_match": direct,
        "retention_priority": priority,
    }
    values.update(updates)
    return ContextCandidate.model_validate(values)


def service(
    tmp_path: Path, *, maximum: int = 700, strategy: str = "conservative-mixed-text-v2", now: datetime = NOW
) -> ContextPackingService:
    profiles = ModelProfileStore(tmp_path / "profiles")
    profiles.save(profile(maximum=maximum, strategy=strategy))
    return ContextPackingService(
        profiles,
        ContextBudgetCalculator(),
        manifest_store=ContextManifestStore(tmp_path / "manifests"),
        clock=lambda: now,
    )


def test_ranking_is_repeated_input_order_independent_and_mandatory_first() -> None:
    values = (
        candidate("history", category=CandidateCategory.CONVERSATION_HISTORY),
        candidate("direct", direct=True, category=CandidateCategory.SOURCE_CODE_EXCERPT),
        candidate("mandatory", mandatory=True, category=CandidateCategory.SYSTEM_INSTRUCTIONS),
        candidate("task", category=CandidateCategory.CURRENT_TASK),
    )
    ranker = ContextCandidateRanker(ConservativeTextEstimator())
    first = ranker.rank(values)
    second = ranker.rank(tuple(reversed(values)))
    assert first == second
    assert [item.candidate_id for item in first.ranked] == ["mandatory", "task", "direct", "history"]


def test_score_components_cover_category_dependency_change_test_recency_and_retention() -> None:
    values = (
        candidate(
            "signals",
            category=CandidateCategory.TESTS,
            priority=RetentionPriority.HIGH,
            dependency_distance=1,
            change_state=ChangeState.MODIFIED,
            relevance=RelevanceSignals(task_relevance=7, recency_rank=6, source_confidence=5),
        ),
        candidate("plain", category=CandidateCategory.MISCELLANEOUS),
    )
    ranked = ContextCandidateRanker(ConservativeTextEstimator()).rank(values).ranked
    assert ranked[0].candidate_id == "signals"
    assert ranked[0].component_scores["dependency_distance"] > 0
    assert ranked[0].component_scores["changed_file"] > 0
    assert ranked[0].component_scores["test_or_error"] > 0
    assert ranked[0].component_scores["recency"] == 6
    assert ranked[0].component_scores["retention"] > ranked[1].component_scores["retention"]


def test_stable_ties_and_missing_metadata() -> None:
    ranked = ContextCandidateRanker(ConservativeTextEstimator()).rank((candidate("b"), candidate("a"))).ranked
    assert [item.candidate_id for item in ranked] == ["a", "b"]


def test_deduplication_winner_rules_and_visibility() -> None:
    shared = "same content"
    values = (
        candidate("weak", 30, content=shared, priority=RetentionPriority.NORMAL),
        candidate("winner", 20, content=shared, mandatory=True, direct=True, priority=RetentionPriority.HIGH),
        candidate("logical-a", deduplication_identity="entity:one"),
        candidate("logical-b", deduplication_identity="entity:one", direct=True),
        candidate("range-a", source_path="src/app.py", line_start=1, line_end=3),
        candidate("range-b", source_path="SRC/APP.PY", line_start=1, line_end=3),
    )
    result = ContextCandidateRanker(ConservativeTextEstimator()).rank(values)
    assert "winner" in {item.candidate_id for item in result.ranked}
    assert "logical-b" in {item.candidate_id for item in result.ranked}
    assert len(result.ranked) == 3
    assert len(result.excluded) == 3
    assert all(item.reason == ExclusionReason.DUPLICATE for item in result.excluded)
    assert next(item for item in result.excluded if item.candidate_id == "weak").duplicate_of == "winner"


def test_duplicate_ids_are_not_silent() -> None:
    result = ContextCandidateRanker(ConservativeTextEstimator()).rank(
        (candidate("same", 20, content="one"), candidate("same", 10, content="two"))
    )
    assert len(result.ranked) == 1
    assert result.ranked[0].token_count == 10
    assert result.excluded[0].reason == ExclusionReason.DUPLICATE


@pytest.mark.parametrize(
    ("values", "expected_tokens"),
    [
        ((), 0),
        ((candidate("required", 100, mandatory=True),), 100),
        (
            (
                candidate("a", 300, category=CandidateCategory.TESTS),
                candidate("b", 400, category=CandidateCategory.BUILD_OR_COMPILER_ERROR),
            ),
            700,
        ),
        ((candidate("a", 701),), 0),
        ((candidate("zero", 0),), 0),
    ],
)
def test_packing_empty_mandatory_boundary_overflow_and_zero(
    tmp_path: Path, values: tuple[ContextCandidate, ...], expected_tokens: int
) -> None:
    manifest = service(tmp_path).pack("demo:latest", values, digest="sha256:test")
    assert manifest.included_token_total == expected_tokens
    assert manifest.included_token_total <= manifest.available_pack_tokens


def test_oversized_optional_does_not_starve_later_smaller_candidate(tmp_path: Path) -> None:
    manifest = service(tmp_path, maximum=100).pack(
        "demo:latest",
        (candidate("large", 101, direct=True), candidate("small", 40)),
        digest="sha256:test",
    )
    assert [item.candidate.candidate_id for item in manifest.included] == ["small"]
    assert (
        next(item for item in manifest.excluded if item.candidate_id == "large").reason
        == ExclusionReason.BUDGET_EXCEEDED
    )


def test_caller_reserved_input_reduces_pack_and_overreservation_is_invalid(tmp_path: Path) -> None:
    packing = service(tmp_path, maximum=100)
    reduced = packing.pack(
        "demo:latest",
        (candidate("candidate", 71, category=CandidateCategory.TESTS),),
        digest="sha256:test",
        caller_reserved_input_tokens=30,
    )
    assert reduced.available_pack_tokens == 70
    assert reduced.included == ()
    assert reduced.excluded[0].reason == ExclusionReason.BUDGET_EXCEEDED

    invalid = packing.pack(
        "demo:latest",
        (candidate("candidate", 1),),
        digest="sha256:test",
        caller_reserved_input_tokens=101,
    )
    assert invalid.decision == ManifestDecision.INVALID_REQUEST
    assert invalid.available_pack_tokens == 0


def test_mandatory_overflow_is_visible_and_never_partially_packed(tmp_path: Path) -> None:
    manifest = service(tmp_path, maximum=100).pack(
        "demo:latest",
        (candidate("m1", 60, mandatory=True), candidate("m2", 41, mandatory=True), candidate("optional", 1)),
        digest="sha256:test",
    )
    assert manifest.decision == ManifestDecision.MANDATORY_OVERFLOW
    assert manifest.included == ()
    assert manifest.token_deficit == 1
    assert {item.candidate_id for item in manifest.excluded} == {"m1", "m2", "optional"}


def test_category_cap_preserves_diversity_but_direct_match_is_exempt(tmp_path: Path) -> None:
    manifest = service(tmp_path, maximum=100).pack(
        "demo:latest",
        (
            candidate("docs-1", 50, category=CandidateCategory.REPOSITORY_DOCUMENTATION),
            candidate("docs-2", 20, category=CandidateCategory.REPOSITORY_DOCUMENTATION),
            candidate("docs-direct", 20, category=CandidateCategory.REPOSITORY_DOCUMENTATION, direct=True),
            candidate("test", 20, category=CandidateCategory.TESTS),
        ),
        digest="sha256:test",
    )
    assert "docs-direct" in {item.candidate.candidate_id for item in manifest.included}
    assert any(item.reason == ExclusionReason.CATEGORY_CAP for item in manifest.excluded)


def test_measured_and_estimated_counts_and_both_profile_strategies(tmp_path: Path) -> None:
    for strategy in ("conservative-mixed-text-v2", "normalized-utf8-byte-upper-bound-v1"):
        root = tmp_path / strategy
        manifest = service(root, strategy=strategy).pack(
            "demo:latest",
            (
                candidate("measured", 7),
                ContextCandidate(
                    candidate_id="estimated",
                    category=CandidateCategory.CURRENT_TASK,
                    label="estimated",
                    content="hello world",
                ),
            ),
            digest="sha256:test",
        )
        by_id = {item.candidate.candidate_id: item.candidate for item in manifest.included}
        assert by_id["measured"].token_provenance == TokenCountProvenance.MEASURED
        assert by_id["estimated"].token_provenance == TokenCountProvenance.HEURISTIC
        assert manifest.estimator_strategy == strategy


def test_exact_digest_weak_stale_and_advertised_capacity_behavior(tmp_path: Path) -> None:
    packing = service(tmp_path, maximum=100)
    with pytest.raises(ModelProfileDigestMismatchError):
        packing.pack("demo:latest", (), digest="sha256:other")
    manifest = packing.pack("demo:latest", (candidate("too-large", 101),), digest="sha256:test")
    assert manifest.available_pack_tokens == 100
    assert manifest.operational_context_tokens == 400
    assert manifest.model_identity.model_digest == "sha256:test"

    weak_store = ModelProfileStore(tmp_path / "weak")
    weak_store.save(profile(maximum=100, digest=None))
    weak = ContextPackingService(weak_store, ContextBudgetCalculator())
    with pytest.raises(ModelProfileNotFoundError, match="weak identity"):
        weak.pack("demo:latest", ())

    stale_store = ModelProfileStore(tmp_path / "stale")
    stale_store.save(profile(maximum=100).model_copy(update={"calibration_status": CalibrationStatus.STALE}))
    stale = ContextPackingService(stale_store, ContextBudgetCalculator())
    with pytest.raises(ModelProfileNotFoundError, match="stale"):
        stale.pack("demo:latest", ())


def test_negative_counts_and_invalid_candidate_shapes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        candidate("negative", -1)
    with pytest.raises(ValidationError, match="requires token_count"):
        ContextCandidate(
            candidate_id="ref", category=CandidateCategory.CURRENT_TASK, label="ref", logical_source="task"
        )


def test_manifest_fingerprint_ignores_timestamp_but_serialization_is_deterministic(tmp_path: Path) -> None:
    crlf = ContextCandidate(
        candidate_id="a",
        category=CandidateCategory.CURRENT_TASK,
        label="a",
        content="line one\r\nline two\r\n",
    )
    lf = crlf.model_copy(update={"content": "line one\nline two\n"})
    first = service(tmp_path / "one", now=NOW).pack("demo:latest", (crlf,), digest="sha256:test")
    second = service(tmp_path / "two", now=NOW + timedelta(days=1)).pack("demo:latest", (lf,), digest="sha256:test")
    assert first.manifest_fingerprint == second.manifest_fingerprint
    assert first.manifest_id == second.manifest_id
    assert ContextManifestStore.serialize(first) == ContextManifestStore.serialize(first)
    assert first.calculated_at != second.calculated_at


def test_manifest_atomic_persistence_list_load_malformed_and_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packing = service(tmp_path)
    manifest = packing.pack("demo:latest", (candidate("a"),), digest="sha256:test", persist=True)
    store = packing.manifest_store
    assert store is not None
    assert store.load(manifest.manifest_id) == manifest
    assert store.list_manifests() == [manifest]
    assert b"content-a" not in ContextManifestStore.serialize(manifest)

    import infinitecontex.context_packing.store as store_module

    observed: list[tuple[Path, Path]] = []
    real_replace = store_module.os.replace

    def record_replace(source: str | Path, destination: str | Path) -> None:
        observed.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", record_replace)
    store.save(manifest)
    assert observed[0][0].parent == observed[0][1].parent

    (store.directory / "bad.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(ContextManifestFormatError, match="malformed"):
        store.list_manifests()
    (store.directory / "bad.json").write_bytes(orjson.dumps({"schema_version": 99}))
    with pytest.raises(ContextManifestFormatError, match="Unsupported"):
        store.list_manifests()
    with pytest.raises(ContextManifestNotFoundError):
        store.load("../escape")


def test_large_candidate_set_is_deterministic_and_complete(tmp_path: Path) -> None:
    values = tuple(candidate(f"candidate-{index:05d}", 1) for index in range(5000))
    first = service(tmp_path / "one", maximum=3000).pack("demo:latest", values, digest="sha256:test")
    second = service(tmp_path / "two", maximum=3000).pack("demo:latest", tuple(reversed(values)), digest="sha256:test")
    assert first.manifest_fingerprint == second.manifest_fingerprint
    assert len(first.included) + len(first.excluded) == 5000
    assert first.included_token_total <= 3000
