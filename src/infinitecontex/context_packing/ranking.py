"""Deterministic candidate accounting, deduplication, and ranking."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import orjson

from infinitecontex.context_budget.estimation import TokenEstimator, normalize_text
from infinitecontex.context_budget.models import TokenCountProvenance
from infinitecontex.context_packing.errors import ContextPackingError
from infinitecontex.context_packing.models import (
    CandidateCategory,
    ChangeState,
    ContextCandidate,
    ExclusionReason,
    ManifestExcludedCandidate,
    RankedCandidate,
    RankingPolicy,
)


@dataclass(frozen=True)
class RankingResult:
    ranked: tuple[RankedCandidate, ...]
    excluded: tuple[ManifestExcludedCandidate, ...]


@dataclass(frozen=True)
class _AccountedCandidate:
    source: ContextCandidate
    token_count: int
    provenance: TokenCountProvenance
    estimation_strategy: str
    estimation_version: int | None
    content_hash: str
    fingerprint: str


class ContextCandidateRanker:
    def __init__(self, estimator: TokenEstimator, policy: RankingPolicy | None = None) -> None:
        self.estimator = estimator
        self.policy = policy or RankingPolicy()

    def rank(self, candidates: tuple[ContextCandidate, ...]) -> RankingResult:
        accounted = tuple(self._account(candidate) for candidate in candidates)
        eligible = tuple(item for item in accounted if not item.source.excluded)
        exclusions = [self._explicit_exclusion(item) for item in accounted if item.source.excluded]
        unique, duplicate_exclusions = self._deduplicate(eligible)
        exclusions.extend(duplicate_exclusions)
        ordered = sorted(unique, key=self._ranking_key)
        ranked = tuple(self._to_ranked(item, index + 1) for index, item in enumerate(ordered))
        return RankingResult(ranked=ranked, excluded=tuple(sorted(exclusions, key=_exclusion_key)))

    def _account(self, candidate: ContextCandidate) -> _AccountedCandidate:
        if candidate.content is not None:
            normalized = normalize_text(candidate.content)
            content_hash = hashlib.sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()
            if candidate.content_hash is not None and candidate.content_hash != content_hash:
                raise ContextPackingError(f"Candidate {candidate.candidate_id} content_hash does not match its content")
        else:
            content_hash = candidate.content_hash or _reference_hash(candidate)

        if candidate.token_count is not None:
            token_count = candidate.token_count
            provenance = TokenCountProvenance.MEASURED
            strategy = "explicit-token-count"
            version = None
        else:
            assert candidate.content is not None
            estimate = self.estimator.estimate(candidate.content)
            token_count = estimate.token_count
            provenance = estimate.provenance
            strategy = estimate.strategy_name
            version = estimate.strategy_version

        fingerprint_payload = {
            "candidate": candidate.model_dump(
                mode="json",
                exclude={"content", "content_hash", "observed_at"},
            ),
            "observed_at": candidate.observed_at.isoformat() if candidate.observed_at else None,
            "content_hash": content_hash,
            "token_count": token_count,
            "provenance": provenance,
            "estimation_strategy": strategy,
            "estimation_version": version,
        }
        fingerprint = hashlib.sha256(orjson.dumps(fingerprint_payload, option=orjson.OPT_SORT_KEYS)).hexdigest()
        return _AccountedCandidate(
            source=candidate,
            token_count=token_count,
            provenance=provenance,
            estimation_strategy=strategy,
            estimation_version=version,
            content_hash=content_hash,
            fingerprint=fingerprint,
        )

    def _deduplicate(
        self, candidates: tuple[_AccountedCandidate, ...]
    ) -> tuple[tuple[_AccountedCandidate, ...], tuple[ManifestExcludedCandidate, ...]]:
        parents = list(range(len(candidates)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        owners: dict[str, int] = {}
        for index, candidate in enumerate(candidates):
            for key in _deduplication_keys(candidate):
                owner = owners.setdefault(key, index)
                union(index, owner)

        groups: dict[int, list[_AccountedCandidate]] = {}
        for index, candidate in enumerate(candidates):
            groups.setdefault(find(index), []).append(candidate)

        winners: list[_AccountedCandidate] = []
        exclusions: list[ManifestExcludedCandidate] = []
        for group in groups.values():
            ordered = sorted(group, key=_duplicate_winner_key)
            winner = ordered[0]
            winners.append(winner)
            for duplicate in ordered[1:]:
                exclusions.append(
                    ManifestExcludedCandidate(
                        candidate_id=duplicate.source.candidate_id,
                        category=duplicate.source.category,
                        label=duplicate.source.label,
                        token_count=duplicate.token_count,
                        candidate_fingerprint=duplicate.fingerprint,
                        reason=ExclusionReason.DUPLICATE,
                        detail=f"Duplicate of {winner.source.candidate_id}",
                        duplicate_of=winner.source.candidate_id,
                    )
                )
        return tuple(winners), tuple(exclusions)

    def _ranking_key(self, item: _AccountedCandidate) -> tuple[int, int, str, str, str]:
        tier = _tier(item.source)
        score = sum(self._component_scores(item.source).values())
        return (tier, -score, item.source.tie_break_key, item.source.candidate_id, item.fingerprint)

    def _to_ranked(self, item: _AccountedCandidate, final_rank: int) -> RankedCandidate:
        components = self._component_scores(item.source)
        return RankedCandidate(
            candidate_id=item.source.candidate_id,
            category=item.source.category,
            label=item.source.label,
            final_rank=final_rank,
            tier=_tier(item.source),
            total_score=sum(components.values()),
            component_scores=dict(sorted(components.items())),
            tie_break_values=(item.source.tie_break_key, item.source.candidate_id, item.fingerprint),
            token_count=item.token_count,
            token_provenance=item.provenance,
            estimation_strategy=item.estimation_strategy,
            estimation_version=item.estimation_version,
            mandatory=item.source.mandatory,
            direct_request_match=item.source.direct_request_match,
            retention_priority=item.source.retention_priority,
            group_id=item.source.group_id,
            source_path=item.source.source_path,
            source_range=_source_range(item.source),
            logical_source=item.source.logical_source,
            symbol_id=item.source.symbol_id,
            content_hash=item.content_hash,
            candidate_fingerprint=item.fingerprint,
        )

    def _component_scores(self, candidate: ContextCandidate) -> dict[str, int]:
        policy = self.policy
        dependency = 0
        if candidate.dependency_distance is not None:
            dependency = max(
                0,
                policy.dependency_base_weight - candidate.dependency_distance * policy.dependency_step_penalty,
            )
        return {
            "category": policy.category_priority[candidate.category],
            "changed_file": (
                policy.changed_file_weight
                if candidate.change_state in {ChangeState.MODIFIED, ChangeState.ADDED, ChangeState.DELETED}
                else 0
            ),
            "dependency_distance": dependency,
            "direct_match": policy.direct_match_weight if candidate.direct_request_match else 0,
            "recency": candidate.relevance.recency_rank * policy.recency_weight,
            "retention": int(candidate.retention_priority) * policy.retention_weight,
            "source_confidence": candidate.relevance.source_confidence * policy.source_confidence_weight,
            "task_relevance": candidate.relevance.task_relevance * policy.task_relevance_weight,
            "test_or_error": (
                policy.test_error_weight
                if candidate.category in {CandidateCategory.TESTS, CandidateCategory.BUILD_OR_COMPILER_ERROR}
                else 0
            ),
        }

    @staticmethod
    def _explicit_exclusion(item: _AccountedCandidate) -> ManifestExcludedCandidate:
        return ManifestExcludedCandidate(
            candidate_id=item.source.candidate_id,
            category=item.source.category,
            label=item.source.label,
            token_count=item.token_count,
            candidate_fingerprint=item.fingerprint,
            reason=ExclusionReason.EXPLICITLY_EXCLUDED,
            detail=item.source.exclusion_reason or "Candidate was explicitly excluded by the caller",
        )


def _tier(candidate: ContextCandidate) -> int:
    if candidate.mandatory:
        return 0
    if candidate.category in {
        CandidateCategory.CURRENT_TASK,
        CandidateCategory.DIRECT_USER_REQUEST,
        CandidateCategory.SYSTEM_INSTRUCTIONS,
        CandidateCategory.PROJECT_INSTRUCTIONS,
    }:
        return 1
    if candidate.direct_request_match:
        return 2
    if candidate.category in {CandidateCategory.TESTS, CandidateCategory.BUILD_OR_COMPILER_ERROR}:
        return 3
    if candidate.change_state in {ChangeState.MODIFIED, ChangeState.ADDED, ChangeState.DELETED}:
        return 4
    if candidate.category in {
        CandidateCategory.DEPENDENCY_CONTEXT,
        CandidateCategory.SOURCE_CODE_EXCERPT,
        CandidateCategory.SYMBOL_DEFINITION,
        CandidateCategory.GIT_DIFF,
    }:
        return 5
    if candidate.category in {CandidateCategory.DECISION_MEMORY, CandidateCategory.TASK_STATE}:
        return 6
    if candidate.category == CandidateCategory.REPOSITORY_DOCUMENTATION:
        return 7
    return 8


def _deduplication_keys(item: _AccountedCandidate) -> tuple[str, ...]:
    candidate = item.source
    keys = [f"id:{candidate.candidate_id}", f"content:{item.content_hash}"]
    if candidate.deduplication_identity:
        keys.append(f"logical:{candidate.deduplication_identity}")
    source_range = _source_range(candidate)
    if candidate.source_path and source_range:
        keys.append(f"range:{candidate.source_path.casefold()}:{source_range}")
    return tuple(keys)


def _duplicate_winner_key(item: _AccountedCandidate) -> tuple[int, int, int, int, int, str]:
    candidate = item.source
    return (
        -int(candidate.mandatory),
        -int(candidate.direct_request_match),
        -int(candidate.retention_priority),
        -int(item.provenance == TokenCountProvenance.MEASURED),
        item.token_count,
        item.fingerprint,
    )


def _source_range(candidate: ContextCandidate) -> str | None:
    if candidate.line_start is None or candidate.line_end is None:
        return None
    return f"{candidate.line_start}:{candidate.line_end}"


def _reference_hash(candidate: ContextCandidate) -> str:
    payload = {
        "logical_source": candidate.logical_source,
        "source_path": candidate.source_path,
        "source_range": _source_range(candidate),
        "symbol_id": candidate.symbol_id,
    }
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


def _exclusion_key(item: ManifestExcludedCandidate) -> tuple[str, str, str]:
    return (item.candidate_id, item.reason, item.candidate_fingerprint)
