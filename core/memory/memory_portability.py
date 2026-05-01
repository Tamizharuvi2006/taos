from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Dict, List

from .import_review_model import ImportReviewSession
from .memory_exporter import export_ai_profile
from .memory_import_analyzer import analyze_memory_import
from .sensitive_memory_filter import check_sensitive_memory
from .user_memory_model import UserMemory
from .user_memory_store import GLOBAL_USER_MEMORY_STORE, UserMemoryStore


IMPORT_TO_MEMORY_TYPE = {
    "project_context": "project_memory",
    "temporary_context": "chat_summary",
    "user_identity_preference": "saved_memory",
    "assistant_style": "saved_memory",
    "career_goal": "saved_memory",
    "technical_stack": "saved_memory",
    "workflow_preference": "saved_memory",
    "active_task": "saved_memory",
    "long_term_preference": "saved_memory",
}


@dataclass
class ImportConfirmSelection:
    item_id: str
    selected: bool = True
    content: str = ""
    type: str = ""
    allow_sensitive: bool = False


class MemoryPortabilityService:
    def __init__(self, memory_store: UserMemoryStore) -> None:
        self.memory_store = memory_store
        self._sessions: Dict[str, ImportReviewSession] = {}
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        self._lock = RLock()

    def export_profile(self, user_id: str, *, preferred_name: str = "") -> Dict[str, Any]:
        memories = self.memory_store.list(user_id)
        return export_ai_profile(memories, preferred_name=preferred_name)

    def analyze_import(self, user_id: str, text: str) -> ImportReviewSession:
        session = analyze_memory_import(text, user_id=user_id)
        with self._lock:
            self._sessions[session.id] = session
            self._history.setdefault(user_id, []).append(
                {
                    "import_session_id": session.id,
                    "status": session.status,
                    "created_at": session.created_at,
                    "summary": session.summary,
                }
            )
        return session

    def confirm_import(self, user_id: str, session_id: str, selections: List[ImportConfirmSelection]) -> Dict[str, Any]:
        session = self._get_user_session(user_id, session_id)
        selection_map = {selection.item_id: selection for selection in selections}
        saved: List[UserMemory] = []
        blocked: List[Dict[str, Any]] = []
        for item in session.items:
            selection = selection_map.get(item.id)
            selected = selection.selected if selection is not None else (item.selected if not selections else False)
            if not selected:
                continue
            if item.risk == "blocked" or item.type == "blocked_sensitive":
                blocked.append({"item_id": item.id, "reason": item.reason or "Blocked sensitive item."})
                continue
            content = (selection.content if selection and selection.content else item.content).strip()
            import_type = selection.type if selection and selection.type else item.type
            sensitive = check_sensitive_memory(content, explicit_user_request=bool(selection and selection.allow_sensitive))
            if not sensitive.allowed:
                blocked.append({"item_id": item.id, "reason": sensitive.reason})
                continue
            memory = UserMemory(
                user_id=user_id,
                type=IMPORT_TO_MEMORY_TYPE.get(import_type, "saved_memory"),
                content=content,
                source_chat_id=session.id,
                source_message_id=item.id,
                confidence=item.confidence,
                tags=[import_type, "imported"],
                reason_saved="Imported by user after review",
            ).validate()
            self.memory_store.create(memory)
            saved.append(memory)
        session.status = "confirmed"
        self._append_history(user_id, session, saved_count=len(saved), blocked_count=len(blocked))
        return {
            "import_session_id": session.id,
            "saved": [memory.to_dict() for memory in saved],
            "blocked": blocked,
            "saved_count": len(saved),
        }

    def cancel_import(self, user_id: str, session_id: str) -> Dict[str, Any]:
        session = self._get_user_session(user_id, session_id)
        session.status = "cancelled"
        self._append_history(user_id, session, saved_count=0, blocked_count=0)
        return {"import_session_id": session.id, "status": session.status}

    def history(self, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._history.get(user_id, []))

    def reset(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._history.clear()

    def _get_user_session(self, user_id: str, session_id: str) -> ImportReviewSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None or session.user_id != user_id:
            raise KeyError(session_id)
        return session

    def _append_history(self, user_id: str, session: ImportReviewSession, *, saved_count: int, blocked_count: int) -> None:
        with self._lock:
            self._history.setdefault(user_id, []).append(
                {
                    "import_session_id": session.id,
                    "status": session.status,
                    "created_at": session.created_at,
                    "summary": session.summary,
                    "saved_count": saved_count,
                    "blocked_count": blocked_count,
                }
            )


GLOBAL_MEMORY_PORTABILITY = MemoryPortabilityService(memory_store=GLOBAL_USER_MEMORY_STORE)
