"""Versioned conservative bounds for deterministic task splitting."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SplitPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: Literal["bounded-structural-split-v1"] = "bounded-structural-split-v1"
    version: Literal[1] = 1
    maximum_depth: int = Field(default=4, ge=1, le=8)
    maximum_direct_children: int = Field(default=16, ge=2, le=32)
    maximum_descendants: int = Field(default=64, ge=2, le=256)
    minimum_partitions: int = Field(default=2, ge=2)
    preferred_target_utilization_basis_points: int = Field(default=7500, ge=1000, le=9500)
    allow_optional_fitting_proposals: bool = True
    approval_required: bool = True
    shared_context_strategy: Literal["explicit_copy"] = "explicit_copy"
    dependency_strategy: Literal["completion_barrier"] = "completion_barrier"
    fail_closed: Literal[True] = True
