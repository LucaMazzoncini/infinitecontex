"""Fail-closed persisted-manifest verification."""

from __future__ import annotations

from infinitecontex.context_admission.errors import FingerprintMismatchAdmissionError, MalformedManifestAdmissionError
from infinitecontex.context_admission.policy import AdmissionPolicy
from infinitecontex.context_packing.fingerprints import compute_manifest_fingerprint
from infinitecontex.context_packing.models import ContextManifest, ExclusionReason
from infinitecontex.model_profiles.models import ModelProfile


def verify_manifest(manifest: ContextManifest, profile: ModelProfile, policy: AdmissionPolicy) -> None:
    fingerprint = compute_manifest_fingerprint(manifest)
    if fingerprint != manifest.manifest_fingerprint:
        raise FingerprintMismatchAdmissionError("Manifest fingerprint does not match its canonical content")
    if manifest.manifest_id != f"context-manifest-{fingerprint[:24]}":
        raise FingerprintMismatchAdmissionError("Manifest ID is inconsistent with its fingerprint")
    identity = profile.model_identity
    if manifest.model_profile_id != profile.profile_id:
        raise MalformedManifestAdmissionError("Manifest profile ID does not match the selected profile")
    if (
        manifest.model_identity.provider.casefold() != identity.provider.casefold()
        or manifest.model_identity.normalized_model_name != identity.normalized_model_name
        or manifest.model_identity.model_digest != identity.model_digest
    ):
        raise MalformedManifestAdmissionError("Manifest model identity does not match the selected profile digest")
    if policy.accepted_estimators.get(manifest.estimator_strategy) != manifest.estimator_version:
        raise MalformedManifestAdmissionError("Manifest estimator strategy/version is not admitted by policy")
    if policy.accepted_ranking_policies.get(manifest.ranking_policy_id) != manifest.ranking_policy_version:
        raise MalformedManifestAdmissionError("Manifest ranking policy/version is not admitted by policy")
    if policy.accepted_packing_strategies.get(manifest.packing_strategy_id) != manifest.packing_strategy_version:
        raise MalformedManifestAdmissionError("Manifest packing strategy/version is not admitted by policy")
    if manifest.decision not in policy.accepted_manifest_decisions:
        raise MalformedManifestAdmissionError(f"Manifest decision {manifest.decision} is not admissible")
    if (
        manifest.operational_context_tokens != profile.operational_context_tokens
        or manifest.maximum_recommended_input_tokens != profile.maximum_recommended_input_tokens
    ):
        raise MalformedManifestAdmissionError("Manifest capacity does not match the persisted operational profile")
    included = [item.candidate for item in manifest.included]
    if len({item.candidate_id for item in included}) != len(included) or len(
        {item.candidate_fingerprint for item in included}
    ) != len(included):
        raise MalformedManifestAdmissionError("Manifest contains duplicate included candidate identities")
    if sum(item.token_count for item in included) != manifest.included_token_total:
        raise MalformedManifestAdmissionError("Manifest included-token accounting is inconsistent")
    if sum(item.token_count for item in included if item.mandatory) != manifest.mandatory_token_total:
        raise MalformedManifestAdmissionError("Manifest mandatory-token accounting is inconsistent")
    optional_excluded = sum(
        item.token_count
        for item in manifest.excluded
        if item.reason in {ExclusionReason.BUDGET_EXCEEDED, ExclusionReason.CATEGORY_CAP}
    )
    if (
        sum(item.token_count for item in included if not item.mandatory) + optional_excluded
        != manifest.optional_token_total
    ):
        raise MalformedManifestAdmissionError("Manifest optional-token accounting is inconsistent")
