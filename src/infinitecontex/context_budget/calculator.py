"""Integer-only context-budget arithmetic."""

from __future__ import annotations

from infinitecontex.context_budget.models import (
    BudgetDecision,
    BudgetRequest,
    BudgetResult,
    EstimationConfidence,
    FixedReserves,
    TokenCountProvenance,
)
from infinitecontex.model_profiles.models import CalibrationStatus, IdentityStrength, ModelProfile


class ContextBudgetCalculator:
    def __init__(self, warning_threshold_basis_points: int = 8750) -> None:
        if not 1 <= warning_threshold_basis_points <= 10000:
            raise ValueError("warning threshold must be between 1 and 10000 basis points")
        self.warning_threshold_basis_points = warning_threshold_basis_points

    def calculate(self, profile: ModelProfile, request: BudgetRequest) -> BudgetResult:
        reserves = FixedReserves(
            output_tokens=profile.reserved_output_tokens,
            tool_result_tokens=profile.reserved_tool_result_tokens,
            system_prompt_tokens=profile.reserved_system_prompt_tokens,
            safety_margin_tokens=profile.safety_margin_tokens,
            total_tokens=(
                profile.reserved_output_tokens
                + profile.reserved_tool_result_tokens
                + profile.reserved_system_prompt_tokens
                + profile.safety_margin_tokens
            ),
        )
        warnings: list[str] = []
        invalid_reasons = self._invalid_reasons(profile, request, reserves)
        if profile.calibration_status == CalibrationStatus.UNCALIBRATED:
            warnings.append("Model profile is uncalibrated; capacities are conservative estimates.")

        total_input = sum(section.token_count for section in request.sections)
        maximum_input = profile.maximum_recommended_input_tokens
        remaining = max(0, maximum_input - total_input)
        utilization = (total_input * 10000) // maximum_input if maximum_input > 0 else 10000

        if invalid_reasons:
            warnings.extend(invalid_reasons)
            decision = BudgetDecision.BLOCKED
        elif total_input <= maximum_input:
            decision = (
                BudgetDecision.PASS_HARD
                if utilization >= self.warning_threshold_basis_points
                else BudgetDecision.PASS_TARGET
            )
            if decision == BudgetDecision.PASS_HARD:
                warnings.append("Proposed input fits but is at or above the warning threshold.")
        else:
            removable_tokens = sum(section.token_count for section in request.sections if not section.mandatory)
            if removable_tokens > 0 and total_input - removable_tokens <= maximum_input:
                decision = BudgetDecision.REPACK_REQUIRED
                warnings.append("Input exceeds the recommended maximum; remove optional sections and recalculate.")
            else:
                decision = BudgetDecision.SPLIT_REQUIRED
                warnings.append("Required input exceeds the recommended maximum; split the work before inference.")

        return BudgetResult(
            profile_id=profile.profile_id,
            model_identity=profile.model_identity,
            operational_context_tokens=profile.operational_context_tokens,
            configured_context_tokens=profile.configured_context_tokens,
            advertised_context_tokens=profile.advertised_context_tokens,
            fixed_reserves=reserves,
            maximum_recommended_input_tokens=maximum_input,
            total_proposed_input_tokens=total_input,
            remaining_input_tokens=remaining,
            utilization_basis_points=utilization,
            sections=request.sections,
            estimation_confidence=_estimation_confidence(request),
            warnings=tuple(warnings),
            decision=decision,
            calculated_at=request.calculated_at,
        )

    @staticmethod
    def _invalid_reasons(profile: ModelProfile, request: BudgetRequest, reserves: FixedReserves) -> list[str]:
        reasons: list[str] = []
        identity = profile.model_identity
        requested_identity = request.model_identity
        if request.profile_id != profile.profile_id:
            reasons.append("Budget request profile ID does not match the resolved profile.")
        if (
            identity.provider.casefold() != requested_identity.provider.casefold()
            or identity.normalized_model_name != requested_identity.normalized_model_name
            or identity.model_digest != requested_identity.model_digest
        ):
            reasons.append("Budget request model identity does not match the resolved profile digest.")
        if identity.identity_strength != IdentityStrength.VERIFIED or not identity.model_digest:
            reasons.append("A verified model digest is required for budget calculation.")
        if profile.calibration_status == CalibrationStatus.STALE:
            reasons.append("The selected model profile is stale and must be recreated.")
        if len({section.name for section in request.sections}) != len(request.sections):
            reasons.append("Context section names must be unique.")
        if request.requested_output_tokens > profile.reserved_output_tokens:
            reasons.append("Requested output allowance exceeds the profile reserve.")
        if request.requested_tool_result_tokens > profile.reserved_tool_result_tokens:
            reasons.append("Requested tool-result allowance exceeds the profile reserve.")
        if reserves.total_tokens + profile.maximum_recommended_input_tokens > profile.operational_context_tokens:
            reasons.append("Profile reserves and maximum input exceed operational capacity.")
        if profile.maximum_recommended_input_tokens <= 0:
            reasons.append("Profile maximum recommended input must be positive.")
        return reasons


def _estimation_confidence(request: BudgetRequest) -> EstimationConfidence:
    provenances = {section.count_provenance for section in request.sections}
    if not provenances:
        return EstimationConfidence.NONE
    if provenances == {TokenCountProvenance.MEASURED}:
        return EstimationConfidence.HIGH
    if provenances == {TokenCountProvenance.HEURISTIC}:
        return EstimationConfidence.LOW
    return EstimationConfidence.MIXED
