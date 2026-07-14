"""Application logic for the first read-only chat slice."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from infinitecontex.chat.history import ConversationHistory
from infinitecontex.context_admission.dispatch import GatedChatDispatcher
from infinitecontex.context_admission.errors import AdmissionRejectedError
from infinitecontex.context_admission.models import (
    AdmissionRecord,
    AdmissionRequest,
    AdmissionResult,
    AdmissionSection,
    AdmissionSectionKind,
)
from infinitecontex.context_packing.models import CandidateCategory, ContextCandidate, ContextManifest, RelevanceSignals
from infinitecontex.context_packing.service import ContextPackingService
from infinitecontex.model_profiles.models import ModelProfile
from infinitecontex.service import InfiniteContextService

SYSTEM_MESSAGE = (
    "You are a read-only coding assistant. Do not claim to have modified files or run tools. "
    "Answer from the conversation only and clearly state when repository evidence is not available."
)


class ChatApplication:
    def __init__(
        self,
        project_root: Path,
        dispatcher: GatedChatDispatcher,
        packing_service: ContextPackingService,
        profile: ModelProfile,
        model: str,
        *,
        max_turns: int = 6,
        auto_snapshot: bool = True,
    ) -> None:
        self.project_root = project_root.resolve()
        self.dispatcher = dispatcher
        self.packing_service = packing_service
        self.profile = profile
        self.model = model
        self.history = ConversationHistory(max_turns)
        self.auto_snapshot = auto_snapshot
        self.snapshot_id: str | None = None
        self.snapshot_error: str | None = None
        self.latest_manifest: ContextManifest | None = None
        self.latest_admission: AdmissionRecord | AdmissionResult | None = None

    def start(self) -> None:
        if not self.auto_snapshot:
            return
        try:
            snapshot = InfiniteContextService(self.project_root).snapshot()
            self.snapshot_id = snapshot.id
        except Exception as exc:
            self.snapshot_error = str(exc)

    def handle(self, text: str, emit: Callable[[str], None]) -> bool:
        stripped = text.strip()
        if not stripped:
            return True
        if stripped.startswith("/"):
            return self._slash(stripped, emit)
        history = self.history.messages()
        candidates = [
            ContextCandidate(
                candidate_id="chat-system",
                category=CandidateCategory.SYSTEM_INSTRUCTIONS,
                label="Read-only system instructions",
                content=SYSTEM_MESSAGE,
                mandatory=True,
            ),
            ContextCandidate(
                candidate_id="chat-current-user",
                category=CandidateCategory.DIRECT_USER_REQUEST,
                label="Current user request",
                content=stripped,
                mandatory=True,
                direct_request_match=True,
            ),
        ]
        for index, message in enumerate(history):
            candidates.append(
                ContextCandidate(
                    candidate_id=f"chat-history-{index:04d}",
                    category=CandidateCategory.CONVERSATION_HISTORY,
                    label=f"History {index + 1} ({message.role})",
                    content=message.content,
                    relevance=RelevanceSignals(recency_rank=index + 1),
                )
            )
        manifest = self.packing_service.pack(
            self.model, candidates, digest=self.profile.model_identity.model_digest, persist=True
        )
        self.latest_manifest = manifest
        included = {item.candidate.candidate_id: item.candidate for item in manifest.included}
        excluded_fingerprints = {item.candidate_id: item.candidate_fingerprint for item in manifest.excluded}

        def section(
            candidate_id: str,
            kind: AdmissionSectionKind,
            role: Literal["system", "user", "assistant"],
            content: str,
            mandatory: bool = True,
        ) -> AdmissionSection:
            candidate = included.get(candidate_id)
            fingerprint = candidate.candidate_fingerprint if candidate else excluded_fingerprints[candidate_id]
            return AdmissionSection(
                section_id=candidate_id,
                kind=kind,
                role=role,
                content=content,
                mandatory=candidate.mandatory if candidate else mandatory,
                manifest_candidate_id=candidate_id,
                manifest_candidate_fingerprint=fingerprint,
            )

        history_sections = tuple(
            section(
                f"chat-history-{index:04d}", AdmissionSectionKind.CONVERSATION_HISTORY, message.role, message.content
            )
            for index, message in enumerate(history)
            if f"chat-history-{index:04d}" in included
        )
        request = AdmissionRequest(
            provider=self.profile.model_identity.provider,
            model_name=self.profile.model_identity.model_name,
            normalized_model_name=self.profile.model_identity.normalized_model_name,
            model_digest=self.profile.model_identity.model_digest or "",
            profile_id=self.profile.profile_id,
            manifest_id=manifest.manifest_id,
            system_instructions=section(
                "chat-system", AdmissionSectionKind.SYSTEM_INSTRUCTIONS, "system", SYSTEM_MESSAGE
            ),
            conversation_history=history_sections,
            current_user_request=section(
                "chat-current-user", AdmissionSectionKind.CURRENT_USER_REQUEST, "user", stripped
            ),
            requested_output_tokens=self.profile.reserved_output_tokens,
            requested_tool_result_tokens=self.profile.reserved_tool_result_tokens,
            calculated_at=datetime.now(UTC),
        )
        parts: list[str] = []
        try:
            for chunk in self.dispatcher.stream_chat(request):
                if chunk.content:
                    parts.append(chunk.content)
                    emit(chunk.content)
        except AdmissionRejectedError as exc:
            self.latest_admission = exc.result
            rejection = exc.result.rejections[0]
            emit(f"Admission rejected ({rejection.code}): {rejection.explanation} {rejection.remediation}")
            return True
        self.latest_admission = self.dispatcher.gate.record_store.load(
            self.dispatcher.gate.record_store.list_records()[0].admission_id
        )
        self.history.add_turn(stripped, "".join(parts))
        return True

    def _slash(self, command: str, emit: Callable[[str], None]) -> bool:
        if command == "/quit":
            return False
        if command == "/help":
            emit("/help  /status  /context  /quit")
        elif command == "/status":
            snapshot = self.snapshot_id or (
                f"unavailable ({self.snapshot_error})" if self.snapshot_error else "disabled"
            )
            emit(f"Repo: {self.project_root}\nModel: {self.model}\nMode: read-only\nSnapshot: {snapshot}")
        elif command == "/context":
            manifest = self.latest_manifest
            admission = self.latest_admission
            remaining = manifest.remaining_pack_tokens if manifest else self.profile.maximum_recommended_input_tokens
            reserves = (
                f"output={self.profile.reserved_output_tokens}, "
                f"tools={self.profile.reserved_tool_result_tokens}, "
                f"system={self.profile.reserved_system_prompt_tokens}, "
                f"safety={self.profile.safety_margin_tokens}"
            )
            emit(
                "\n".join(
                    (
                        "Hard runtime gating: active",
                        f"Model: {self.model}",
                        f"Digest: verified ({self.profile.model_identity.model_digest})",
                        f"Profile: {self.profile.profile_id} ({self.profile.calibration_status})",
                        "Admission policy: fail-closed-context-admission-v1",
                        f"Latest manifest: {manifest.manifest_id if manifest else 'none'}",
                        f"Latest admission: {getattr(admission, 'decision', 'none')}",
                        f"Packed tokens: {manifest.included_token_total if manifest else 0}",
                        f"Remaining pack budget: {remaining}",
                        f"Reserves: {reserves}",
                    )
                )
            )
        else:
            emit(f"Unknown command: {command}. Type /help for available commands.")
        return True
