"""Exact-profile context ranking, packing, and manifest construction."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from infinitecontex.context_budget.calculator import ContextBudgetCalculator
from infinitecontex.context_budget.estimation import ConservativeTextEstimator, estimator_for_strategy
from infinitecontex.context_budget.models import ContextSectionCategory, ContextSectionInput
from infinitecontex.context_budget.service import ContextBudgetService
from infinitecontex.context_packing.fingerprints import fingerprint_payload, manifest_fingerprint_payload
from infinitecontex.context_packing.models import (
    CandidateCategory,
    ContextCandidate,
    ContextManifest,
    ExclusionReason,
    ManifestDecision,
    ManifestExcludedCandidate,
    ManifestIncludedCandidate,
    RankedCandidate,
    RankingPolicy,
)
from infinitecontex.context_packing.ranking import ContextCandidateRanker
from infinitecontex.context_packing.store import ContextManifestStore
from infinitecontex.model_profiles.store import ModelProfileStore

BULK_CATEGORIES = {
    CandidateCategory.SOURCE_CODE_EXCERPT,
    CandidateCategory.DEPENDENCY_CONTEXT,
    CandidateCategory.REPOSITORY_DOCUMENTATION,
    CandidateCategory.CONVERSATION_HISTORY,
    CandidateCategory.TOOL_RESULT,
    CandidateCategory.MISCELLANEOUS,
}


class ContextPackingService:
    def __init__(
        self,
        profile_store: ModelProfileStore,
        calculator: ContextBudgetCalculator,
        *,
        manifest_store: ContextManifestStore | None = None,
        policy: RankingPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.profile_store = profile_store
        self.calculator = calculator
        self.manifest_store = manifest_store
        self.policy = policy or RankingPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))

    def pack(
        self,
        model_name: str,
        candidates: Sequence[ContextCandidate],
        *,
        digest: str | None = None,
        caller_reserved_input_tokens: int = 0,
        persist: bool = False,
    ) -> ContextManifest:
        calculated_at = self.clock()
        resolver = ContextBudgetService(
            self.profile_store,
            ConservativeTextEstimator(),
            self.calculator,
            clock=lambda: calculated_at,
        )
        profile = resolver.resolve_profile(model_name, digest)
        estimator = estimator_for_strategy(profile.token_estimation_strategy)
        budget_service = ContextBudgetService(
            self.profile_store,
            estimator,
            self.calculator,
            clock=lambda: calculated_at,
        )
        reservation_sections = (
            [
                ContextSectionInput(
                    name="caller-reserved-input",
                    category=ContextSectionCategory.OTHER,
                    token_count=caller_reserved_input_tokens,
                    mandatory=True,
                )
            ]
            if caller_reserved_input_tokens
            else []
        )
        budget = budget_service.calculate(model_name, reservation_sections, digest=profile.model_identity.model_digest)
        ranking = ContextCandidateRanker(estimator, self.policy).rank(tuple(candidates))
        available = budget.remaining_input_tokens
        mandatory_total = sum(candidate.token_count for candidate in ranking.ranked if candidate.mandatory)
        optional_total = sum(candidate.token_count for candidate in ranking.ranked if not candidate.mandatory)
        warnings = list(budget.warnings)
        exclusions = list(ranking.excluded)
        included: list[ManifestIncludedCandidate] = []
        deficit = 0

        if caller_reserved_input_tokens > budget.maximum_recommended_input_tokens:
            warnings.append("Caller-reserved input already exceeds the maximum recommended input.")
            exclusions.extend(
                self._exclude_all(ranking.ranked, ExclusionReason.INVALID_REQUEST, "No pack budget remains")
            )
            decision = ManifestDecision.INVALID_REQUEST
        elif mandatory_total > available:
            deficit = mandatory_total - available
            warnings.append(
                f"Mandatory candidates exceed the available pack budget by {deficit} tokens; split or reduce the task."
            )
            for candidate in ranking.ranked:
                exclusions.append(
                    _excluded(
                        candidate,
                        ExclusionReason.MANDATORY_OVERFLOW if candidate.mandatory else ExclusionReason.BUDGET_EXCEEDED,
                        (
                            f"Mandatory candidate is part of a set with a {deficit}-token deficit"
                            if candidate.mandatory
                            else "Optional packing was skipped because the mandatory set overflowed"
                        ),
                    )
                )
            decision = ManifestDecision.MANDATORY_OVERFLOW
        else:
            included_tokens = 0
            optional_category_tokens: dict[CandidateCategory, int] = {}
            for candidate in ranking.ranked:
                if candidate.mandatory:
                    included_tokens += candidate.token_count
                    included.append(
                        ManifestIncludedCandidate(
                            candidate=candidate,
                            inclusion_order=len(included) + 1,
                            reason="mandatory candidate",
                        )
                    )
                    continue
                if self._category_cap_exceeded(candidate, optional_category_tokens, available):
                    exclusions.append(
                        _excluded(
                            candidate,
                            ExclusionReason.CATEGORY_CAP,
                            f"Optional {candidate.category} content reached the configured 60% category cap",
                        )
                    )
                    continue
                if included_tokens + candidate.token_count > available:
                    exclusions.append(
                        _excluded(
                            candidate,
                            ExclusionReason.BUDGET_EXCEEDED,
                            "Candidate did not fit; later smaller candidates were still evaluated",
                        )
                    )
                    continue
                included_tokens += candidate.token_count
                optional_category_tokens[candidate.category] = (
                    optional_category_tokens.get(candidate.category, 0) + candidate.token_count
                )
                included.append(
                    ManifestIncludedCandidate(
                        candidate=candidate,
                        inclusion_order=len(included) + 1,
                        reason="ranked candidate fit the remaining deterministic budget",
                    )
                )
            decision = (
                ManifestDecision.OPTIONAL_EXCLUDED
                if exclusions
                else ManifestDecision.PACKED_WITH_WARNING
                if warnings
                else ManifestDecision.PACKED
            )

        included_total = sum(item.candidate.token_count for item in included)
        exclusions.sort(key=lambda item: (item.candidate_id, item.reason, item.candidate_fingerprint))
        estimator_version = estimator.estimate("").strategy_version
        safeguards = {
            "bulk_categories": sorted(category.value for category in BULK_CATEGORIES),
            "direct_match_exempt": True,
            "mandatory_exempt": True,
            "optional_bulk_category_cap_basis_points": self.policy.optional_bulk_category_cap_basis_points,
        }
        fingerprint = fingerprint_payload(
            manifest_fingerprint_payload(
                policy=self.policy,
                estimator_strategy=estimator.strategy_name,
                estimator_version=estimator_version,
                profile_id=profile.profile_id,
                provider=profile.model_identity.provider,
                normalized_model_name=profile.model_identity.normalized_model_name,
                digest=profile.model_identity.model_digest,
                operational_context_tokens=profile.operational_context_tokens,
                maximum_recommended_input_tokens=budget.maximum_recommended_input_tokens,
                caller_reserved_input_tokens=caller_reserved_input_tokens,
                available_pack_tokens=available,
                safeguards=safeguards,
                included=[item.model_dump(mode="json") for item in included],
                excluded=[item.model_dump(mode="json") for item in exclusions],
                decision=decision,
                token_deficit=deficit,
            )
        )
        manifest = ContextManifest(
            manifest_id=f"context-manifest-{fingerprint[:24]}",
            manifest_fingerprint=fingerprint,
            calculated_at=calculated_at,
            ranking_policy_id=self.policy.policy_id,
            ranking_policy_version=self.policy.version,
            packing_strategy_id=self.policy.packing_strategy,
            packing_strategy_version=self.policy.packing_version,
            estimator_strategy=estimator.strategy_name,
            estimator_version=estimator_version,
            model_profile_id=profile.profile_id,
            model_identity=profile.model_identity,
            operational_context_tokens=profile.operational_context_tokens,
            maximum_recommended_input_tokens=budget.maximum_recommended_input_tokens,
            caller_reserved_input_tokens=caller_reserved_input_tokens,
            available_pack_tokens=available,
            mandatory_token_total=mandatory_total,
            optional_token_total=optional_total,
            included_token_total=included_total,
            remaining_pack_tokens=available - included_total,
            token_deficit=deficit,
            safeguards=safeguards,
            included=tuple(included),
            excluded=tuple(exclusions),
            warnings=tuple(warnings),
            decision=decision,
        )
        if persist:
            if self.manifest_store is None:
                raise ValueError("Manifest persistence was requested without a manifest store")
            self.manifest_store.save(manifest)
        return manifest

    def _category_cap_exceeded(
        self,
        candidate: RankedCandidate,
        optional_category_tokens: dict[CandidateCategory, int],
        available: int,
    ) -> bool:
        if candidate.mandatory or candidate.direct_request_match or candidate.category not in BULK_CATEGORIES:
            return False
        cap = (available * self.policy.optional_bulk_category_cap_basis_points) // 10000
        return optional_category_tokens.get(candidate.category, 0) + candidate.token_count > cap

    @staticmethod
    def _exclude_all(
        candidates: tuple[RankedCandidate, ...], reason: ExclusionReason, detail: str
    ) -> list[ManifestExcludedCandidate]:
        return [_excluded(candidate, reason, detail) for candidate in candidates]


def _excluded(candidate: RankedCandidate, reason: ExclusionReason, detail: str) -> ManifestExcludedCandidate:
    return ManifestExcludedCandidate(
        candidate_id=candidate.candidate_id,
        category=candidate.category,
        label=candidate.label,
        token_count=candidate.token_count,
        candidate_fingerprint=candidate.candidate_fingerprint,
        reason=reason,
        detail=detail,
    )
