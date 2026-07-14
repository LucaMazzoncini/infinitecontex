"""Built-in fail-closed admission policy."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from infinitecontex.context_packing.models import ManifestDecision


class AdmissionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: Literal["fail-closed-context-admission-v1"] = "fail-closed-context-admission-v1"
    version: Literal[1] = 1
    fail_closed: Literal[True] = True
    permit_conservative_uncalibrated: bool = True
    maximum_uncalibrated_operational_tokens: int = 32768
    permit_stale_profiles: Literal[False] = False
    accepted_manifest_decisions: frozenset[ManifestDecision] = Field(
        default_factory=lambda: frozenset(
            {
                ManifestDecision.PACKED,
                ManifestDecision.PACKED_WITH_WARNING,
                ManifestDecision.OPTIONAL_EXCLUDED,
            }
        )
    )
    accepted_estimators: dict[str, int] = Field(
        default_factory=lambda: {"normalized-utf8-byte-upper-bound-v1": 1, "conservative-mixed-text-v2": 2}
    )
    accepted_ranking_policies: dict[str, int] = Field(default_factory=lambda: {"deterministic-context-ranking-v1": 1})
    accepted_packing_strategies: dict[str, int] = Field(default_factory=lambda: {"tiered-greedy-pack-v1": 1})
    allow_lower_requested_allowances: bool = True
    provider_message_overhead_tokens: int = 2
    warning_threshold_basis_points: int = 8750
