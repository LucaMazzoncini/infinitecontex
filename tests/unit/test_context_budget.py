from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import orjson
import pytest
from pydantic import ValidationError

from infinitecontex.context_budget.calculator import ContextBudgetCalculator
from infinitecontex.context_budget.estimation import ConservativeTextEstimator
from infinitecontex.context_budget.models import (
    BudgetDecision,
    BudgetRequest,
    ContextSection,
    ContextSectionCategory,
    ContextSectionInput,
    EstimationConfidence,
    RetentionPriority,
    TokenCountProvenance,
)
from infinitecontex.context_budget.service import ContextBudgetService
from infinitecontex.model_profiles.errors import (
    ModelProfileDigestMismatchError,
    ModelProfileFormatError,
    ModelProfileNotFoundError,
)
from infinitecontex.model_profiles.models import (
    CalibrationStatus,
    IdentityStrength,
    ModelIdentity,
    ModelProfile,
    ValueProvenance,
)
from infinitecontex.model_profiles.store import ModelProfileStore

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def make_profile(
    *, digest: str | None = "sha256:test", operational: int = 1000, maximum_input: int = 700
) -> ModelProfile:
    reserve = operational - maximum_input
    output = reserve // 3
    tool = reserve // 3
    system = reserve // 6
    safety = reserve - output - tool - system
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
        profile_id="profile-test",
        model_identity=identity,
        advertised_context_tokens=operational * 2,
        configured_context_tokens=operational,
        operational_context_tokens=operational,
        reserved_output_tokens=output,
        reserved_tool_result_tokens=tool,
        reserved_system_prompt_tokens=system,
        safety_margin_tokens=safety,
        maximum_recommended_input_tokens=maximum_input,
        tokenizer_strategy="unverified",
        token_estimation_strategy="test",
        calibration_status=CalibrationStatus.UNCALIBRATED,
        created_at=NOW,
        updated_at=NOW,
        provenance={
            "advertised_context_tokens": estimated,
            "configured_context_tokens": estimated,
            "operational_context_tokens": estimated,
            "reserved_output_tokens": estimated,
            "reserved_tool_result_tokens": estimated,
            "reserved_system_prompt_tokens": estimated,
            "safety_margin_tokens": estimated,
            "maximum_recommended_input_tokens": estimated,
            "tokenizer_strategy": estimated,
            "token_estimation_strategy": estimated,
        },
    )


def measured(name: str, count: int, *, mandatory: bool = True) -> ContextSection:
    return ContextSection(
        name=name,
        category=ContextSectionCategory.OTHER,
        token_count=count,
        count_provenance=TokenCountProvenance.MEASURED,
        estimation_strategy="explicit-token-count",
        mandatory=mandatory,
        priority=RetentionPriority.REQUIRED if mandatory else RetentionPriority.OPTIONAL,
    )


def request(profile: ModelProfile, sections: tuple[ContextSection, ...]) -> BudgetRequest:
    return BudgetRequest(
        profile_id=profile.profile_id,
        model_identity=profile.model_identity,
        sections=sections,
        requested_output_tokens=profile.reserved_output_tokens,
        requested_tool_result_tokens=profile.reserved_tool_result_tokens,
        calculated_at=NOW,
        estimation_strategy="test",
    )


def test_repeated_calculation_is_deterministic_and_accounts_fixed_reserves() -> None:
    profile = make_profile()
    calculator = ContextBudgetCalculator()
    calculation = request(profile, (measured("system", 100), measured("request", 200)))
    first = calculator.calculate(profile, calculation)
    second = calculator.calculate(profile, calculation)
    assert first == second
    assert first.fixed_reserves.total_tokens == 300
    assert first.maximum_recommended_input_tokens + first.fixed_reserves.total_tokens == 1000
    assert first.total_proposed_input_tokens == 300
    assert first.remaining_input_tokens == 400
    assert first.decision == BudgetDecision.PASS_TARGET


@pytest.mark.parametrize(
    ("count", "decision", "remaining"),
    [
        (0, BudgetDecision.PASS_TARGET, 700),
        (700, BudgetDecision.PASS_HARD, 0),
        (701, BudgetDecision.SPLIT_REQUIRED, 0),
    ],
)
def test_zero_exact_boundary_and_one_token_overflow(count: int, decision: BudgetDecision, remaining: int) -> None:
    profile = make_profile()
    result = ContextBudgetCalculator().calculate(profile, request(profile, (measured("input", count),)))
    assert result.decision == decision
    assert result.remaining_input_tokens == remaining


def test_warning_threshold_boundary_uses_integer_basis_points() -> None:
    profile = make_profile(maximum_input=800)
    calculator = ContextBudgetCalculator(warning_threshold_basis_points=8750)
    below = calculator.calculate(profile, request(profile, (measured("input", 699),)))
    boundary = calculator.calculate(profile, request(profile, (measured("input", 700),)))
    assert below.utilization_basis_points == 8737
    assert below.decision == BudgetDecision.PASS_TARGET
    assert boundary.utilization_basis_points == 8750
    assert boundary.decision == BudgetDecision.PASS_HARD


def test_optional_sections_choose_repack_only_when_removal_is_sufficient() -> None:
    profile = make_profile()
    calculator = ContextBudgetCalculator()
    repack = calculator.calculate(
        profile,
        request(profile, (measured("required", 650), measured("optional", 100, mandatory=False))),
    )
    split = calculator.calculate(
        profile,
        request(profile, (measured("required", 710), measured("optional", 10, mandatory=False))),
    )
    assert repack.decision == BudgetDecision.REPACK_REQUIRED
    assert split.decision == BudgetDecision.SPLIT_REQUIRED


def test_allowance_overages_and_duplicate_names_are_blocked() -> None:
    profile = make_profile()
    base = request(profile, (measured("same", 1), measured("same", 2)))
    duplicate = ContextBudgetCalculator().calculate(profile, base)
    assert duplicate.decision == BudgetDecision.BLOCKED
    assert any("unique" in warning for warning in duplicate.warnings)

    overage = base.model_copy(update={"sections": (), "requested_output_tokens": profile.reserved_output_tokens + 1})
    result = ContextBudgetCalculator().calculate(profile, overage)
    assert result.decision == BudgetDecision.BLOCKED
    assert any("output allowance" in warning for warning in result.warnings)

    tool_overage = base.model_copy(
        update={"sections": (), "requested_tool_result_tokens": profile.reserved_tool_result_tokens + 1}
    )
    assert ContextBudgetCalculator().calculate(profile, tool_overage).decision == BudgetDecision.BLOCKED

    smaller_allowances = base.model_copy(
        update={
            "sections": (),
            "requested_output_tokens": 0,
            "requested_tool_result_tokens": 0,
        }
    )
    covered = ContextBudgetCalculator().calculate(profile, smaller_allowances)
    assert covered.decision == BudgetDecision.PASS_TARGET
    assert covered.maximum_recommended_input_tokens == profile.maximum_recommended_input_tokens


def test_impossible_profile_and_identity_mismatch_are_blocked() -> None:
    profile = make_profile()
    impossible = profile.model_copy(update={"maximum_recommended_input_tokens": 900})
    assert ContextBudgetCalculator().calculate(impossible, request(impossible, ())).decision == BudgetDecision.BLOCKED

    other_identity = profile.model_identity.model_copy(update={"model_digest": "sha256:other"})
    mismatched = request(profile, ()).model_copy(update={"model_identity": other_identity})
    assert ContextBudgetCalculator().calculate(profile, mismatched).decision == BudgetDecision.BLOCKED


def test_negative_counts_and_invalid_section_shapes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ContextSectionInput(name="bad", category=ContextSectionCategory.OTHER, token_count=-1)
    with pytest.raises(ValidationError, match="exactly one"):
        ContextSectionInput(name="bad", category=ContextSectionCategory.OTHER)


def test_unicode_and_line_endings_are_deterministic_and_explicitly_heuristic() -> None:
    estimator = ConservativeTextEstimator()
    assert estimator.estimate("a\r\nb\r") == estimator.estimate("a\nb\n")
    unicode_first = estimator.estimate("caffè 🐍")
    unicode_second = estimator.estimate("caffè 🐍")
    assert unicode_first == unicode_second
    assert unicode_first.provenance == TokenCountProvenance.HEURISTIC
    assert estimator.estimate("").token_count == 0


def test_service_distinguishes_measured_estimated_and_supports_large_integers(tmp_path: Path) -> None:
    huge = 10**80
    huge_profile = make_profile(operational=huge + 300, maximum_input=huge)
    huge_result = ContextBudgetCalculator().calculate(
        huge_profile, request(huge_profile, (measured("huge", huge - 1),))
    )
    assert huge_result.maximum_recommended_input_tokens == huge
    assert huge_result.remaining_input_tokens == 1

    profile = make_profile()
    store = ModelProfileStore(tmp_path)
    store.save(profile)
    service = ContextBudgetService(
        store,
        ConservativeTextEstimator(),
        ContextBudgetCalculator(),
        clock=lambda: NOW,
    )
    result = service.calculate(
        "demo:latest",
        [
            ContextSectionInput(name="measured", category=ContextSectionCategory.SYSTEM_INSTRUCTIONS, token_count=10),
            ContextSectionInput(name="estimated", category=ContextSectionCategory.CURRENT_USER_REQUEST, text="hello"),
        ],
        digest="sha256:test",
    )
    assert result.estimation_confidence == EstimationConfidence.MIXED
    assert [section.count_provenance for section in result.sections] == ["measured", "heuristic"]
    assert result.maximum_recommended_input_tokens == 700


def test_service_refuses_weak_identity_and_propagates_malformed_profile(tmp_path: Path) -> None:
    store = ModelProfileStore(tmp_path)
    store.save(make_profile(digest=None))
    service = ContextBudgetService(store, ConservativeTextEstimator(), ContextBudgetCalculator())
    with pytest.raises(ModelProfileNotFoundError, match="weak identity"):
        service.resolve_profile("demo:latest")

    for path in tmp_path.glob("*.json"):
        path.unlink()
    (tmp_path / "bad.json").write_bytes(orjson.dumps({"schema_version": 99}))
    with pytest.raises(ModelProfileFormatError, match="Unsupported"):
        service.resolve_profile("demo:latest")


def test_service_requires_exact_digest_when_requested(tmp_path: Path) -> None:
    store = ModelProfileStore(tmp_path)
    store.save(make_profile())
    service = ContextBudgetService(store, ConservativeTextEstimator(), ContextBudgetCalculator())
    assert service.resolve_profile("demo:latest", "sha256:test").profile_id == "profile-test"
    with pytest.raises(ModelProfileDigestMismatchError):
        service.resolve_profile("demo:latest", "sha256:other")


def test_service_refuses_stale_profile(tmp_path: Path) -> None:
    stale = make_profile().model_copy(update={"calibration_status": CalibrationStatus.STALE})
    store = ModelProfileStore(tmp_path)
    store.save(stale)
    service = ContextBudgetService(store, ConservativeTextEstimator(), ContextBudgetCalculator())
    with pytest.raises(ModelProfileNotFoundError, match="stale"):
        service.resolve_profile("demo:latest")
