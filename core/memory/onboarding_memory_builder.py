from __future__ import annotations

from threading import RLock
from typing import Any, Dict, List

from .memory_portability import IMPORT_TO_MEMORY_TYPE, ImportConfirmSelection
from .onboarding_analyzer import analyze_onboarding_answers
from .onboarding_model import OnboardingAnswers, OnboardingSession, OnboardingStatus
from .sensitive_memory_filter import check_sensitive_memory
from .user_memory_model import UserMemory, utc_now_iso
from .user_memory_store import UserMemoryStore


class OnboardingMemoryBuilder:
    def __init__(self, memory_store: UserMemoryStore) -> None:
        self.memory_store = memory_store
        self._sessions: Dict[str, OnboardingSession] = {}
        self._statuses: Dict[str, OnboardingStatus] = {}
        self._lock = RLock()

    def status(self, user_id: str) -> OnboardingStatus:
        with self._lock:
            if user_id in self._statuses:
                return self._statuses[user_id]
            has_memories = bool(self.memory_store.list(user_id))
            return OnboardingStatus(user_id=user_id, status="complete" if has_memories else "incomplete")

    def analyze(self, user_id: str, answers: OnboardingAnswers) -> OnboardingSession:
        session = analyze_onboarding_answers(user_id, answers)
        with self._lock:
            self._sessions[user_id] = session
        return session

    def confirm(self, user_id: str, selections: List[ImportConfirmSelection]) -> Dict[str, Any]:
        session = self._sessions.get(user_id)
        if session is None:
            raise KeyError(user_id)
        selection_map = {selection.item_id: selection for selection in selections}
        saved: List[UserMemory] = []
        blocked: List[Dict[str, str]] = []
        for item in session.items:
            selection = selection_map.get(item.id)
            selected = selection.selected if selection is not None else (item.selected if not selections else False)
            if not selected or item.type == "blocked_sensitive":
                continue
            content = (selection.content if selection and selection.content else item.content).strip()
            sensitive = check_sensitive_memory(content, explicit_user_request=bool(selection and selection.allow_sensitive))
            if not sensitive.allowed:
                blocked.append({"item_id": item.id, "reason": sensitive.reason})
                continue
            import_type = selection.type if selection and selection.type else item.type
            memory = UserMemory(
                user_id=user_id,
                type=IMPORT_TO_MEMORY_TYPE.get(import_type, "saved_memory"),
                content=content,
                source_chat_id="onboarding",
                source_message_id=item.id,
                confidence=item.confidence,
                tags=[import_type, "onboarding"],
                reason_saved="Created by user during onboarding review",
            ).validate()
            self.memory_store.create(memory)
            saved.append(memory)
        session.status = "confirmed"
        self._statuses[user_id] = OnboardingStatus(user_id=user_id, status="complete", completed_at=utc_now_iso())
        return {"saved": [memory.to_dict() for memory in saved], "blocked": blocked, "saved_count": len(saved)}

    def skip(self, user_id: str) -> OnboardingStatus:
        status = OnboardingStatus(user_id=user_id, status="skipped", skipped_at=utc_now_iso())
        with self._lock:
            self._statuses[user_id] = status
        return status

    def reset(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._statuses.clear()
