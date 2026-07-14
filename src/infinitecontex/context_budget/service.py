"""Exact-profile resolution, section accounting, and budget calculation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from infinitecontex.context_budget.calculator import ContextBudgetCalculator
from infinitecontex.context_budget.estimation import TokenEstimator
from infinitecontex.context_budget.models import (
    BudgetRequest,
    BudgetResult,
    ContextSection,
    ContextSectionInput,
    TokenCountProvenance,
)
from infinitecontex.model_profiles.errors import ModelProfileDigestMismatchError, ModelProfileNotFoundError
from infinitecontex.model_profiles.models import CalibrationStatus, IdentityStrength, ModelProfile
from infinitecontex.model_profiles.store import ModelProfileStore


class ContextBudgetService:
    def __init__(
        self,
        store: ModelProfileStore,
        estimator: TokenEstimator,
        calculator: ContextBudgetCalculator,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.estimator = estimator
        self.calculator = calculator
        self.clock = clock or (lambda: datetime.now(UTC))

    def resolve_profile(self, model_name: str, digest: str | None = None) -> ModelProfile:
        if digest is not None:
            profile = self.store.find_exact("ollama", model_name, digest)
        else:
            matches = self.store.find_by_name("ollama", model_name)
            if not matches:
                raise ModelProfileNotFoundError(
                    f"No persisted profile exists for ollama/{model_name}; "
                    "create one with `infctx model profile create`"
                )
            if len(matches) > 1:
                raise ModelProfileDigestMismatchError(
                    f"Multiple model digests exist for {model_name}; rerun with --digest"
                )
            profile = matches[0]
        if profile.model_identity.identity_strength != IdentityStrength.VERIFIED:
            raise ModelProfileNotFoundError(
                f"Profile for {model_name} has a weak identity; recreate it when Ollama exposes a digest"
            )
        if profile.calibration_status == CalibrationStatus.STALE:
            raise ModelProfileNotFoundError(f"Profile for {model_name} is stale; recreate it before calculation")
        return profile

    def calculate(
        self,
        model_name: str,
        sections: Sequence[ContextSectionInput],
        *,
        digest: str | None = None,
        requested_output_tokens: int | None = None,
        requested_tool_result_tokens: int | None = None,
    ) -> BudgetResult:
        profile = self.resolve_profile(model_name, digest)
        accounted = tuple(self._account(section) for section in sections)
        request = BudgetRequest(
            profile_id=profile.profile_id,
            model_identity=profile.model_identity,
            sections=accounted,
            requested_output_tokens=(
                profile.reserved_output_tokens if requested_output_tokens is None else requested_output_tokens
            ),
            requested_tool_result_tokens=(
                profile.reserved_tool_result_tokens
                if requested_tool_result_tokens is None
                else requested_tool_result_tokens
            ),
            calculated_at=self.clock(),
            estimation_strategy=self.estimator.strategy_name,
        )
        return self.calculator.calculate(profile, request)

    def _account(self, section: ContextSectionInput) -> ContextSection:
        if section.token_count is not None:
            count = section.token_count
            provenance = TokenCountProvenance.MEASURED
            strategy = "explicit-token-count"
            version = None
            normalization = None
            conservatism = None
        else:
            assert section.text is not None
            estimate = self.estimator.estimate(section.text)
            count = estimate.token_count
            provenance = estimate.provenance
            strategy = self.estimator.strategy_name
            version = estimate.strategy_version
            normalization = estimate.normalization
            conservatism = estimate.conservatism
        return ContextSection(
            name=section.name,
            category=section.category,
            token_count=count,
            count_provenance=provenance,
            estimation_strategy=strategy,
            estimation_version=version,
            normalization=normalization,
            conservatism=conservatism,
            source_ref=section.source_ref,
            mandatory=section.mandatory,
            priority=section.priority,
        )
