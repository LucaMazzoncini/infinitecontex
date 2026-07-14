"""Deterministic fail-closed runtime admission gate."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from infinitecontex.context_admission.errors import (
    FingerprintMismatchAdmissionError,
    MalformedManifestAdmissionError,
    RequestContentMismatchError,
)
from infinitecontex.context_admission.models import (
    AdmissionCheck,
    AdmissionDecision,
    AdmissionRecord,
    AdmissionRejection,
    AdmissionRequest,
    AdmissionResult,
    AdmissionSection,
    AdmissionSectionAccounting,
    AdmissionSectionKind,
    AdmittedEnvelope,
)
from infinitecontex.context_admission.policy import AdmissionPolicy
from infinitecontex.context_admission.store import AdmissionRecordStore
from infinitecontex.context_admission.verification import verify_manifest
from infinitecontex.context_budget.calculator import ContextBudgetCalculator
from infinitecontex.context_budget.estimation import estimator_for_strategy, normalize_text
from infinitecontex.context_budget.models import (
    BudgetDecision,
    BudgetRequest,
    ContextSection,
    ContextSectionCategory,
    RetentionPriority,
    TokenCountProvenance,
)
from infinitecontex.context_packing.errors import ContextManifestFormatError, ContextManifestNotFoundError
from infinitecontex.context_packing.fingerprints import fingerprint_payload
from infinitecontex.context_packing.store import ContextManifestStore
from infinitecontex.model_profiles.errors import (
    ModelProfileDigestMismatchError,
    ModelProfileFormatError,
    ModelProfileNotFoundError,
)
from infinitecontex.model_profiles.models import CalibrationStatus, IdentityStrength, ModelProfile
from infinitecontex.model_profiles.store import ModelProfileStore

EventSink = Callable[[str, dict[str, str]], None]


@dataclass(frozen=True)
class _Failure:
    decision: AdmissionDecision
    code: str
    explanation: str
    remediation: str
    expected: str | int | None = None
    actual: str | int | None = None


class ContextAdmissionGate:
    def __init__(
        self,
        profile_store: ModelProfileStore,
        manifest_store: ContextManifestStore,
        record_store: AdmissionRecordStore,
        *,
        policy: AdmissionPolicy | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.profile_store = profile_store
        self.manifest_store = manifest_store
        self.record_store = record_store
        self.policy = policy or AdmissionPolicy()
        self.event_sink = event_sink or (lambda _event, _fields: None)

    def admit(self, request: AdmissionRequest) -> AdmittedEnvelope:
        result = self.evaluate(request)
        return AdmittedEnvelope(result=result, model_name=request.model_name, sections=request.ordered_sections())

    def evaluate(self, request: AdmissionRequest) -> AdmissionResult:
        checks: list[AdmissionCheck] = []
        self._event("admission_started", request, None)
        try:
            profile = self.profile_store.find_exact(request.provider, request.model_name, request.model_digest)
        except ModelProfileDigestMismatchError as exc:
            try:
                named = self.profile_store.find_by_name(request.provider, request.model_name)
            except ModelProfileFormatError:
                named = []
            if named and all(item.model_identity.identity_strength == IdentityStrength.WEAK for item in named):
                return self._reject(
                    request,
                    _Failure(
                        AdmissionDecision.REJECTED_WEAK_IDENTITY,
                        "weak_identity",
                        "The persisted model identity has no verified digest.",
                        "Recreate the profile when Ollama exposes a digest.",
                    ),
                    checks,
                )
            return self._reject(
                request,
                _Failure(
                    AdmissionDecision.REJECTED_PROFILE_MISMATCH,
                    "profile_digest_mismatch",
                    str(exc),
                    "Create and select a profile for the installed model digest.",
                ),
                checks,
            )
        except ModelProfileNotFoundError as exc:
            return self._reject(
                request,
                _Failure(
                    AdmissionDecision.REJECTED_MISSING_PROFILE,
                    "missing_profile",
                    str(exc),
                    "Run `infctx model profile create <model>` while that exact build is installed.",
                ),
                checks,
            )
        except ModelProfileFormatError as exc:
            return self._reject(
                request,
                _Failure(
                    AdmissionDecision.INVALID_REQUEST,
                    "malformed_profile",
                    str(exc),
                    "Move the malformed profile aside and recreate it.",
                ),
                checks,
            )
        checks.append(AdmissionCheck(code="profile_resolved", passed=True, detail=profile.profile_id))
        failure = self._validate_profile(request, profile)
        if failure:
            return self._reject(request, failure, checks, profile=profile)
        try:
            manifest = self.manifest_store.load(request.manifest_id)
            verify_manifest(manifest, profile, self.policy)
        except ContextManifestNotFoundError as exc:
            return self._reject(
                request,
                _Failure(
                    AdmissionDecision.REJECTED_MISSING_MANIFEST,
                    "missing_manifest",
                    str(exc),
                    "Create and persist a context manifest before dispatch.",
                ),
                checks,
                profile=profile,
            )
        except (ContextManifestFormatError, MalformedManifestAdmissionError) as exc:
            return self._reject(
                request,
                _Failure(
                    AdmissionDecision.REJECTED_INVALID_MANIFEST,
                    "invalid_manifest",
                    str(exc),
                    "Recreate the manifest from trusted candidates.",
                ),
                checks,
                profile=profile,
            )
        except FingerprintMismatchAdmissionError as exc:
            return self._reject(
                request,
                _Failure(
                    AdmissionDecision.REJECTED_INVALID_MANIFEST,
                    "manifest_fingerprint_mismatch",
                    str(exc),
                    "Discard the tampered manifest and create a new one.",
                ),
                checks,
                profile=profile,
            )
        checks.append(AdmissionCheck(code="manifest_verified", passed=True, detail=manifest.manifest_fingerprint))
        try:
            self._verify_correspondence(request, manifest)
        except RequestContentMismatchError as exc:
            return self._reject(
                request,
                _Failure(
                    AdmissionDecision.REJECTED_CONTENT_MISMATCH,
                    "request_content_mismatch",
                    str(exc),
                    "Repack the exact frozen outbound content and retry.",
                ),
                checks,
                profile=profile,
                manifest_fingerprint=manifest.manifest_fingerprint,
            )
        checks.append(
            AdmissionCheck(code="content_correspondence", passed=True, detail="all included candidates matched")
        )
        estimator = estimator_for_strategy(profile.token_estimation_strategy)
        accountings: list[AdmissionSectionAccounting] = []
        budget_sections: list[ContextSection] = []
        for section in request.ordered_sections():
            estimate = estimator.estimate(section.content)
            content_hash = _content_hash(section.content)
            accountings.append(
                AdmissionSectionAccounting(
                    section_id=section.section_id,
                    kind=section.kind,
                    role=section.role,
                    token_count=estimate.token_count,
                    provenance=estimate.provenance,
                    estimator_strategy=estimate.strategy_name,
                    estimator_version=estimate.strategy_version,
                    normalized_content_hash=content_hash,
                    manifest_candidate_id=section.manifest_candidate_id,
                )
            )
            budget_sections.append(
                ContextSection(
                    name=section.section_id,
                    category=_category(section),
                    token_count=estimate.token_count,
                    count_provenance=estimate.provenance,
                    estimation_strategy=estimate.strategy_name,
                    estimation_version=estimate.strategy_version,
                    normalization=estimate.normalization,
                    conservatism=estimate.conservatism,
                    mandatory=section.mandatory,
                    priority=RetentionPriority.REQUIRED if section.mandatory else RetentionPriority.NORMAL,
                )
            )
        overhead = len(accountings) * self.policy.provider_message_overhead_tokens
        accountings.append(
            AdmissionSectionAccounting(
                section_id="provider-message-overhead",
                kind=AdmissionSectionKind.PROVIDER_OVERHEAD,
                role="system",
                token_count=overhead,
                provenance=TokenCountProvenance.MEASURED,
                estimator_strategy="ollama-message-overhead-v1",
                estimator_version=1,
                normalized_content_hash=_content_hash(""),
            )
        )
        budget_sections.append(
            ContextSection(
                name="provider-message-overhead",
                category=ContextSectionCategory.OTHER,
                token_count=overhead,
                count_provenance=TokenCountProvenance.MEASURED,
                estimation_strategy="ollama-message-overhead-v1",
                estimation_version=1,
                mandatory=True,
                priority=RetentionPriority.REQUIRED,
            )
        )
        budget = ContextBudgetCalculator(self.policy.warning_threshold_basis_points).calculate(
            profile,
            BudgetRequest(
                profile_id=profile.profile_id,
                model_identity=profile.model_identity,
                sections=tuple(budget_sections),
                requested_output_tokens=request.requested_output_tokens,
                requested_tool_result_tokens=request.requested_tool_result_tokens,
                calculated_at=request.calculated_at,
                estimation_strategy=estimator.strategy_name,
            ),
        )
        request_fingerprint = _request_fingerprint(request, accountings, manifest.manifest_fingerprint)
        checks.append(AdmissionCheck(code="request_fingerprinted", passed=True, detail=request_fingerprint))
        if (
            request.requested_output_tokens > profile.reserved_output_tokens
            or request.requested_tool_result_tokens > profile.reserved_tool_result_tokens
        ):
            return self._reject(
                request,
                _Failure(
                    AdmissionDecision.REJECTED_ALLOWANCE_OVERFLOW,
                    "allowance_overflow",
                    "Requested output or tool-result allowance exceeds the persisted reserve.",
                    "Lower the requested allowance or create a new conservative profile.",
                ),
                checks,
                profile=profile,
                manifest_fingerprint=manifest.manifest_fingerprint,
                request_fingerprint=request_fingerprint,
                budget=budget,
                sections=tuple(accountings),
            )
        if budget.decision not in {BudgetDecision.PASS_TARGET, BudgetDecision.PASS_HARD}:
            return self._reject(
                request,
                _Failure(
                    AdmissionDecision.REJECTED_BUDGET_OVERFLOW,
                    "budget_overflow",
                    "The exact outbound request exceeds the operational input budget.",
                    "Reduce optional history or split the request.",
                    budget.maximum_recommended_input_tokens,
                    budget.total_proposed_input_tokens,
                ),
                checks,
                profile=profile,
                manifest_fingerprint=manifest.manifest_fingerprint,
                request_fingerprint=request_fingerprint,
                budget=budget,
                sections=tuple(accountings),
            )
        warnings = list(budget.warnings)
        if profile.calibration_status == CalibrationStatus.UNCALIBRATED:
            warnings.append(
                "Admission uses a verified, conservative, uncalibrated profile; this is not hardware calibration."
            )
        decision = AdmissionDecision.ADMITTED_WITH_WARNING if warnings else AdmissionDecision.ADMITTED
        result = self._result(
            request,
            decision,
            checks,
            profile=profile,
            manifest_fingerprint=manifest.manifest_fingerprint,
            request_fingerprint=request_fingerprint,
            budget=budget,
            sections=tuple(accountings),
            warnings=tuple(dict.fromkeys(warnings)),
        )
        self._persist(result, request.model_digest)
        self._event("admission_accepted", request, request_fingerprint)
        return result

    def mark_dispatch(self, result: AdmissionResult, state: str) -> None:
        record = self.record_store.load(result.admission_id)
        self.record_store.save(record.model_copy(update={"dispatch_state": state}))

    def _validate_profile(self, request: AdmissionRequest, profile: ModelProfile) -> _Failure | None:
        identity = profile.model_identity
        if (
            request.profile_id != profile.profile_id
            or request.normalized_model_name != identity.normalized_model_name
            or request.provider.casefold() != identity.provider.casefold()
            or request.model_digest != identity.model_digest
        ):
            return _Failure(
                AdmissionDecision.REJECTED_PROFILE_MISMATCH,
                "profile_identity_mismatch",
                "Admission request does not match the exact persisted profile identity.",
                "Use the exact profile ID, provider, model name, and digest.",
            )
        if identity.identity_strength != IdentityStrength.VERIFIED or not identity.model_digest:
            return _Failure(
                AdmissionDecision.REJECTED_WEAK_IDENTITY,
                "weak_identity",
                "Hard admission requires a verified model digest.",
                "Recreate the profile when Ollama exposes a digest.",
            )
        if profile.calibration_status == CalibrationStatus.STALE and not self.policy.permit_stale_profiles:
            return _Failure(
                AdmissionDecision.REJECTED_STALE_PROFILE,
                "stale_profile",
                "The selected model profile is stale.",
                "Inspect the current model build and recreate its profile.",
            )
        if profile.calibration_status == CalibrationStatus.UNCALIBRATED and (
            not self.policy.permit_conservative_uncalibrated
            or profile.operational_context_tokens > self.policy.maximum_uncalibrated_operational_tokens
        ):
            return _Failure(
                AdmissionDecision.REJECTED_POLICY,
                "uncalibrated_profile_policy",
                "The uncalibrated profile is outside the conservative admission policy.",
                "Use a verified conservative profile at or below 32K, or calibrate in a future milestone.",
            )
        return None

    @staticmethod
    def _verify_correspondence(request: AdmissionRequest, manifest: object) -> None:
        from infinitecontex.context_packing.models import ContextManifest

        assert isinstance(manifest, ContextManifest)
        expected = {item.candidate.candidate_id: item.candidate for item in manifest.included}
        linked: dict[str, AdmissionSection] = {}
        for section in request.ordered_sections():
            if section.manifest_candidate_id is None:
                continue
            if section.manifest_candidate_id in linked:
                raise RequestContentMismatchError("A manifest candidate is represented more than once")
            candidate = expected.get(section.manifest_candidate_id)
            if candidate is None or candidate.candidate_fingerprint != section.manifest_candidate_fingerprint:
                raise RequestContentMismatchError(
                    f"Candidate {section.manifest_candidate_id} is not the admitted manifest candidate"
                )
            if candidate.content_hash != _content_hash(section.content):
                raise RequestContentMismatchError(
                    f"Candidate {section.manifest_candidate_id} content changed after packing"
                )
            linked[section.manifest_candidate_id] = section
        if set(linked) != set(expected):
            raise RequestContentMismatchError(
                "Outbound request does not contain exactly the manifest's included candidates"
            )

    def _reject(
        self, request: AdmissionRequest, failure: _Failure, checks: list[AdmissionCheck], **kwargs: Any
    ) -> AdmissionResult:
        checks.append(AdmissionCheck(code=failure.code, passed=False, detail=failure.explanation))
        result = self._result(
            request,
            failure.decision,
            checks,
            rejections=(
                AdmissionRejection(
                    code=failure.code,
                    explanation=failure.explanation,
                    expected=failure.expected,
                    actual=failure.actual,
                    remediation=failure.remediation,
                ),
            ),
            **kwargs,
        )
        self._persist(result, request.model_digest)
        self._event("admission_rejected", request, result.request_fingerprint)
        return result

    def _result(
        self,
        request: AdmissionRequest,
        decision: AdmissionDecision,
        checks: list[AdmissionCheck],
        *,
        profile: ModelProfile | None = None,
        manifest_fingerprint: str | None = None,
        request_fingerprint: str | None = None,
        budget: object | None = None,
        sections: tuple[AdmissionSectionAccounting, ...] = (),
        warnings: tuple[str, ...] = (),
        rejections: tuple[AdmissionRejection, ...] = (),
    ) -> AdmissionResult:
        from infinitecontex.context_budget.models import BudgetResult

        calculated = budget if isinstance(budget, BudgetResult) else None
        id_hash = fingerprint_payload(
            {
                "timestamp": request.calculated_at.isoformat(),
                "correlation_id": request.correlation_id,
                "decision": decision,
                "request": request_fingerprint,
                "manifest": request.manifest_id,
            }
        )
        return AdmissionResult(
            admission_id=f"context-admission-{id_hash[:24]}",
            decision=decision,
            admitted=decision in {AdmissionDecision.ADMITTED, AdmissionDecision.ADMITTED_WITH_WARNING},
            warning=decision == AdmissionDecision.ADMITTED_WITH_WARNING,
            policy_id=self.policy.policy_id,
            policy_version=self.policy.version,
            model_identity=profile.model_identity if profile else None,
            profile_id=request.profile_id,
            manifest_id=request.manifest_id,
            manifest_fingerprint=manifest_fingerprint,
            request_fingerprint=request_fingerprint,
            operational_context_tokens=calculated.operational_context_tokens
            if calculated
            else (profile.operational_context_tokens if profile else 0),
            maximum_recommended_input_tokens=calculated.maximum_recommended_input_tokens
            if calculated
            else (profile.maximum_recommended_input_tokens if profile else 0),
            actual_estimated_input_tokens=calculated.total_proposed_input_tokens if calculated else 0,
            fixed_reserves=calculated.fixed_reserves if calculated else None,
            remaining_input_tokens=calculated.remaining_input_tokens if calculated else 0,
            requested_output_tokens=request.requested_output_tokens,
            requested_tool_result_tokens=request.requested_tool_result_tokens,
            sections=sections,
            checks=tuple(checks),
            warnings=warnings,
            rejections=rejections,
            calculated_at=request.calculated_at,
            correlation_id=request.correlation_id,
        )

    def _persist(self, result: AdmissionResult, digest: str) -> None:
        self.record_store.save(
            AdmissionRecord(
                admission_id=result.admission_id,
                request_fingerprint=result.request_fingerprint,
                manifest_id=result.manifest_id,
                manifest_fingerprint=result.manifest_fingerprint,
                profile_id=result.profile_id,
                model_digest=digest,
                decision=result.decision,
                admitted=result.admitted,
                actual_estimated_input_tokens=result.actual_estimated_input_tokens,
                remaining_input_tokens=result.remaining_input_tokens,
                reason_codes=tuple(item.code for item in result.rejections),
                warnings=result.warnings,
                timestamp=result.calculated_at,
                correlation_id=result.correlation_id,
            )
        )

    def _event(self, event: str, request: AdmissionRequest, fingerprint: str | None) -> None:
        self.event_sink(
            event, {"correlation_id": request.correlation_id or "", "request_fingerprint": fingerprint or ""}
        )


def _content_hash(content: str) -> str:
    return hashlib.sha256(normalize_text(content).encode("utf-8", errors="surrogatepass")).hexdigest()


def _request_fingerprint(
    request: AdmissionRequest, sections: list[AdmissionSectionAccounting], manifest_fingerprint: str
) -> str:
    return fingerprint_payload(
        {
            "policy": [request.admission_policy_id, request.admission_policy_version],
            "model": [request.provider.casefold(), request.normalized_model_name, request.model_digest],
            "profile_id": request.profile_id,
            "manifest_fingerprint": manifest_fingerprint,
            "sections": [item.model_dump(mode="json") for item in sections],
            "allowances": [request.requested_output_tokens, request.requested_tool_result_tokens],
        }
    )


def _category(section: AdmissionSection) -> ContextSectionCategory:
    mapping = {
        "system_instructions": ContextSectionCategory.SYSTEM_INSTRUCTIONS,
        "project_instructions": ContextSectionCategory.PROJECT_INSTRUCTIONS,
        "conversation_history": ContextSectionCategory.CONVERSATION_HISTORY,
        "current_user_request": ContextSectionCategory.CURRENT_USER_REQUEST,
        "tool_definitions": ContextSectionCategory.TOOL_DEFINITIONS,
        "tool_results": ContextSectionCategory.TOOL_RESULTS,
    }
    return mapping.get(section.kind.value, ContextSectionCategory.OTHER)
