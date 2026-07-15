"""Authoritative mapping to planning capability declarations."""

from __future__ import annotations

from collections.abc import Iterable

from infinitecontex.planning.models import Capability
from infinitecontex.tools.errors import UnknownCapabilityError

POLICY_CAPABILITIES = frozenset(Capability)


def capability_from_name(value: str) -> Capability:
    try:
        return Capability(value)
    except ValueError as exc:
        raise UnknownCapabilityError(f"Unknown capability {value!r}; use a planning capability name") from exc


def normalize_capabilities(values: Iterable[Capability | str]) -> tuple[Capability, ...]:
    normalized = tuple(capability_from_name(value) if isinstance(value, str) else value for value in values)
    if any(value not in POLICY_CAPABILITIES for value in normalized):
        raise UnknownCapabilityError("Policy received a capability outside the authoritative planning vocabulary")
    return tuple(sorted(set(normalized), key=lambda item: item.value))


def capability_gaps(
    requested: Iterable[Capability], required: Iterable[Capability]
) -> tuple[tuple[Capability, ...], tuple[Capability, ...]]:
    requested_set = set(normalize_capabilities(requested))
    required_set = set(normalize_capabilities(required))
    missing_requested = tuple(sorted(required_set - requested_set, key=lambda item: item.value))
    # G1 has no grants. Every required capability is therefore missing from grants.
    missing_granted = tuple(sorted(required_set, key=lambda item: item.value))
    return missing_requested, missing_granted
