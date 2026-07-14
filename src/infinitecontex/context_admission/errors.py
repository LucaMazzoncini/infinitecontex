"""Typed failures for runtime context admission."""

from __future__ import annotations

from infinitecontex.context_admission.models import AdmissionResult


class ContextAdmissionError(RuntimeError):
    pass


class AdmissionRejectedError(ContextAdmissionError):
    def __init__(self, result: AdmissionResult) -> None:
        self.result = result
        explanation = result.rejections[0].explanation if result.rejections else result.decision
        super().__init__(str(explanation))


class MissingProfileAdmissionError(ContextAdmissionError):
    pass


class WeakIdentityAdmissionError(ContextAdmissionError):
    pass


class DigestMismatchAdmissionError(ContextAdmissionError):
    pass


class StaleProfileAdmissionError(ContextAdmissionError):
    pass


class MissingManifestAdmissionError(ContextAdmissionError):
    pass


class MalformedManifestAdmissionError(ContextAdmissionError):
    pass


class FingerprintMismatchAdmissionError(ContextAdmissionError):
    pass


class UnsupportedAdmissionSchemaError(ContextAdmissionError):
    pass


class RequestContentMismatchError(ContextAdmissionError):
    pass


class AdmissionBudgetOverflowError(ContextAdmissionError):
    pass


class AdmissionAllowanceOverflowError(ContextAdmissionError):
    pass


class UnaccountedContentAdmissionError(ContextAdmissionError):
    pass


class DispatchWithoutAdmissionError(ContextAdmissionError):
    pass


class AdmissionRecordPersistenceError(ContextAdmissionError):
    pass
