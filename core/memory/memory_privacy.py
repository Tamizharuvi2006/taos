from __future__ import annotations

from threading import RLock
from typing import Any, Dict, List

from .memory_deletion_service import MemoryDeletionService
from .memory_export_bundle import export_all_memory_bundle
from .memory_retention_policy import MemoryPrivacySettings
from .user_memory_model import UserMemory, utc_now_iso
from .user_memory_store import GLOBAL_USER_MEMORY_STORE, UserMemoryStore


class MemoryPrivacyService:
    def __init__(self, store: UserMemoryStore) -> None:
        self.store = store
        self.deletion = MemoryDeletionService(store)
        self._settings: Dict[str, MemoryPrivacySettings] = {}
        self._audit: Dict[str, List[Dict[str, Any]]] = {}
        self._lock = RLock()

    def settings(self, user_id: str) -> MemoryPrivacySettings:
        with self._lock:
            return self._settings.setdefault(user_id, MemoryPrivacySettings())

    def update_settings(self, user_id: str, **changes) -> MemoryPrivacySettings:
        settings = self.settings(user_id).update(**changes)
        self.store.update_settings(
            user_id,
            memory_enabled=settings.memory_enabled,
            reference_chat_history_enabled=settings.reference_chat_history,
        )
        self._log(user_id, "privacy_settings_updated", {"settings": settings.to_dict()})
        return settings

    def export_all(self, user_id: str) -> Dict[str, Any]:
        self._log(user_id, "export_all", {})
        return export_all_memory_bundle(self.store, user_id)

    def delete_all(self, user_id: str, *, hard: bool = False) -> Dict[str, int]:
        result = self.deletion.delete_all(user_id, hard=hard)
        self._log(user_id, "delete_all", {"hard": hard, "count": result["deleted"]})
        return result

    def delete_imported(self, user_id: str, *, hard: bool = False) -> Dict[str, int]:
        result = self.deletion.delete_imported(user_id, hard=hard)
        self._log(user_id, "delete_imported", {"hard": hard, "count": result["deleted"]})
        return result

    def delete_project(self, user_id: str, project_id: str, *, hard: bool = False) -> Dict[str, int]:
        result = self.deletion.delete_project(user_id, project_id, hard=hard)
        self._log(user_id, "delete_project", {"hard": hard, "project_id": project_id, "count": result["deleted"]})
        return result

    def retrievable_memories(self, user_id: str, query: str = "") -> List[UserMemory]:
        settings = self.settings(user_id)
        if not settings.memory_enabled:
            return []
        memories = self.store.retrieve(user_id, query)
        if not settings.reference_chat_history:
            memories = [memory for memory in memories if memory.type != "chat_summary"]
        if not settings.project_memory_enabled:
            memories = [memory for memory in memories if memory.type != "project_memory"]
        return memories

    def audit_log(self, user_id: str) -> List[Dict[str, Any]]:
        return list(self._audit.get(user_id, []))

    def reset(self) -> None:
        with self._lock:
            self._settings.clear()
            self._audit.clear()

    def _log(self, user_id: str, action: str, details: Dict[str, Any]) -> None:
        safe_details = {key: value for key, value in details.items() if key not in {"content", "memories"}}
        self._audit.setdefault(user_id, []).append({"timestamp": utc_now_iso(), "action": action, "details": safe_details})

GLOBAL_MEMORY_PRIVACY = MemoryPrivacyService(GLOBAL_USER_MEMORY_STORE)
