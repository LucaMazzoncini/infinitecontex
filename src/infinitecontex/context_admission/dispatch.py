"""Admission-owned streaming dispatch of an exact frozen payload."""

from __future__ import annotations

from collections.abc import Iterable

from infinitecontex.context_admission.errors import (
    AdmissionRecordPersistenceError,
    AdmissionRejectedError,
    DispatchWithoutAdmissionError,
)
from infinitecontex.context_admission.gate import ContextAdmissionGate
from infinitecontex.context_admission.models import AdmissionRequest
from infinitecontex.llm.base import LLMClient
from infinitecontex.llm.models import ChatChunk, ChatMessage


class GatedChatDispatcher:
    def __init__(self, gate: ContextAdmissionGate, client: LLMClient) -> None:
        self.gate = gate
        self.client = client

    def stream_chat(self, request: AdmissionRequest) -> Iterable[ChatChunk]:
        envelope = self.gate.admit(request)
        if not envelope.result.admitted:
            raise AdmissionRejectedError(envelope.result)
        if envelope.result.request_fingerprint is None:
            raise DispatchWithoutAdmissionError("An admitted request must have an exact request fingerprint")
        messages = tuple(ChatMessage(role=section.role, content=section.content) for section in envelope.sections)
        completed = False
        try:
            for chunk in self.client.stream_chat(envelope.model_name, messages):
                yield chunk
            completed = True
            self.gate.mark_dispatch(envelope.result, "completed")
        except BaseException:
            if not completed:
                try:
                    self.gate.mark_dispatch(envelope.result, "failed")
                except AdmissionRecordPersistenceError:
                    # Preserve the original transport or cancellation failure.
                    pass
            raise
