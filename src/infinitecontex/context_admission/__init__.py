"""Fail-closed context admission and gated model dispatch."""

from infinitecontex.context_admission.dispatch import GatedChatDispatcher
from infinitecontex.context_admission.gate import ContextAdmissionGate
from infinitecontex.context_admission.models import AdmissionRequest, AdmissionResult

__all__ = ["AdmissionRequest", "AdmissionResult", "ContextAdmissionGate", "GatedChatDispatcher"]
