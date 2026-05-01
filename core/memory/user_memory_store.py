from __future__ import annotations

from dataclasses import dataclass, field
import re
from threading import RLock
from typing import Any, Dict, List

from .user_memory_model import UserMemory, utc_now_iso, visible_active


@dataclass
class UserMemorySettings:
    memory_enabled: bool = True
    reference_chat_history_enabled: bool = True
    updated_at: str = field(default_factory=utc_now_iso)

    def update(self, **changes: Any) -> "UserMemorySettings":
        if "memory_enabled" in changes and changes["memory_enabled"] is not None:
            self.memory_enabled = bool(changes["memory_enabled"])
        if "reference_chat_history_enabled" in changes and changes["reference_chat_history_enabled"] is not None:
            self.reference_chat_history_enabled = bool(changes["reference_chat_history_enabled"])
        self.updated_at = utc_now_iso()
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_enabled": self.memory_enabled,
            "reference_chat_history_enabled": self.reference_chat_history_enabled,
            "updated_at": self.updated_at,
        }


class UserMemoryStore:
    def __init__(self) -> None:
        self._memories: Dict[str, Dict[str, UserMemory]] = {}
        self._settings: Dict[str, UserMemorySettings] = {}
        self._audit: Dict[str, List[Dict[str, Any]]] = {}
        self._lock = RLock()

    def settings_for(self, user_id: str) -> UserMemorySettings:
        with self._lock:
            return self._settings.setdefault(user_id, UserMemorySettings())

    def update_settings(self, user_id: str, **changes: Any) -> UserMemorySettings:
        with self._lock:
            settings = self.settings_for(user_id).update(**changes)
            self._log(user_id, "settings_updated", "", changes)
            return settings

    def create(self, memory: UserMemory) -> UserMemory:
        memory.validate()
        with self._lock:
            self._memories.setdefault(memory.user_id, {})[memory.id] = memory
            self._log(memory.user_id, "created", memory.id, memory.to_dict())
            return memory

    def list(self, user_id: str, *, include_deleted: bool = False, user_visible_only: bool = True) -> List[UserMemory]:
        with self._lock:
            rows = list(self._memories.get(user_id, {}).values())
        if not include_deleted:
            rows = [row for row in rows if row.status != "deleted"]
        if user_visible_only:
            rows = [row for row in rows if row.user_visible]
        return sorted(rows, key=lambda row: row.updated_at, reverse=True)

    def get(self, user_id: str, memory_id: str) -> UserMemory | None:
        with self._lock:
            return self._memories.get(user_id, {}).get(memory_id)

    def update(self, user_id: str, memory_id: str, **changes: Any) -> UserMemory:
        with self._lock:
            memory = self.get(user_id, memory_id)
            if memory is None or memory.status == "deleted":
                raise KeyError(memory_id)
            memory.update(**changes)
            self._log(user_id, "updated", memory_id, changes)
            return memory

    def delete(self, user_id: str, memory_id: str, *, hard: bool = False) -> bool:
        with self._lock:
            if memory_id not in self._memories.get(user_id, {}):
                return False
            if hard:
                self._memories[user_id].pop(memory_id, None)
            else:
                self._memories[user_id][memory_id].update(status="deleted")
            self._log(user_id, "deleted", memory_id, {"hard": hard})
            return True

    def delete_all(self, user_id: str, *, hard: bool = False) -> int:
        with self._lock:
            ids = [
                memory_id
                for memory_id, memory in self._memories.get(user_id, {}).items()
                if hard or memory.status != "deleted"
            ]
            for memory_id in ids:
                self.delete(user_id, memory_id, hard=hard)
            self._log(user_id, "deleted_all", "", {"count": len(ids), "hard": hard})
            return len(ids)

    def retrieve(self, user_id: str, query: str, *, top_k: int = 5) -> List[UserMemory]:
        settings = self.settings_for(user_id)
        if not settings.memory_enabled:
            return []
        query_terms = _terms(query)
        if not query_terms:
            rows = visible_active(self.list(user_id))
        else:
            scored: List[tuple[float, UserMemory]] = []
            for memory in visible_active(self.list(user_id)):
                terms = _terms(" ".join([memory.content, " ".join(memory.tags), memory.type]))
                overlap = len(query_terms & terms)
                if overlap <= 0:
                    continue
                score = overlap / max(1, len(query_terms | terms))
                scored.append((score + memory.confidence * 0.05, memory))
            scored.sort(key=lambda item: item[0], reverse=True)
            rows = [memory for _, memory in scored]
        for memory in rows[:top_k]:
            memory.mark_used()
            self._log(user_id, "used", memory.id, {"query": query})
        return rows[:top_k]

    def audit(self, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._audit.get(user_id, []))

    def reset(self) -> None:
        with self._lock:
            self._memories.clear()
            self._settings.clear()
            self._audit.clear()

    def _log(self, user_id: str, action: str, memory_id: str, details: Dict[str, Any]) -> None:
        self._audit.setdefault(user_id, []).append(
            {
                "timestamp": utc_now_iso(),
                "action": action,
                "memory_id": memory_id,
                "details": details,
            }
        )


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9_]+", str(text or "").lower()) if len(term) >= 3}


GLOBAL_USER_MEMORY_STORE = UserMemoryStore()
