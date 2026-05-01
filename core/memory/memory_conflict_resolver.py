from __future__ import annotations

from threading import RLock
from typing import Any, Dict, List

from .memory_conflict_detector import detect_memory_conflicts
from .memory_conflict_model import MemoryConflict, RESOLUTION_ACTIONS
from .user_memory_model import UserMemory, utc_now_iso
from .user_memory_store import GLOBAL_USER_MEMORY_STORE, UserMemoryStore


class MemoryConflictResolver:
    def __init__(self, store: UserMemoryStore) -> None:
        self.store = store
        self._conflicts: Dict[str, Dict[str, MemoryConflict]] = {}
        self._lock = RLock()

    def detect(self, user_id: str, candidates: List[UserMemory]) -> List[MemoryConflict]:
        conflicts = detect_memory_conflicts(user_id, self.store.list(user_id), candidates)
        with self._lock:
            bucket = self._conflicts.setdefault(user_id, {})
            for conflict in conflicts:
                bucket[conflict.id] = conflict
        return conflicts

    def list(self, user_id: str) -> List[MemoryConflict]:
        with self._lock:
            return list(self._conflicts.get(user_id, {}).values())

    def resolve(self, user_id: str, conflict_id: str, action: str, *, merged_content: str = "") -> Dict[str, Any]:
        if action not in RESOLUTION_ACTIONS:
            raise ValueError(action)
        conflict = self._conflicts.get(user_id, {}).get(conflict_id)
        if conflict is None:
            raise KeyError(conflict_id)
        old = self.store.get(user_id, conflict.old_memory_id)
        new = self.store.get(user_id, conflict.new_memory_id)
        created = None
        if action == "keep_new":
            if old:
                self.store.update(user_id, old.id, status="disabled")
        elif action == "keep_old":
            if new:
                self.store.update(user_id, new.id, status="disabled")
        elif action == "disable_both":
            if old:
                self.store.update(user_id, old.id, status="disabled")
            if new:
                self.store.update(user_id, new.id, status="disabled")
        elif action == "mark_old_stale":
            if old:
                self.store.update(user_id, old.id, status="disabled", tags=list(set(old.tags + ["stale"])))
        elif action in {"merge", "edit_manually"}:
            content = merged_content.strip() or conflict.suggested_resolution or conflict.new_content
            if old:
                self.store.update(user_id, old.id, status="disabled")
            if new:
                self.store.update(user_id, new.id, status="disabled")
            created = self.store.create(
                UserMemory(
                    user_id=user_id,
                    content=content,
                    type="saved_memory",
                    source_chat_id="memory_conflict",
                    source_message_id=conflict.id,
                    tags=["conflict_resolved"],
                    reason_saved=f"Created by resolving memory conflict via {action}.",
                )
            )
        conflict.status = f"resolved:{action}"
        conflict.resolved_at = utc_now_iso()
        return {"conflict": conflict.to_dict(), "created_memory": created.to_dict() if created else None}

    def reset(self) -> None:
        with self._lock:
            self._conflicts.clear()


GLOBAL_MEMORY_CONFLICT_RESOLVER = MemoryConflictResolver(store=GLOBAL_USER_MEMORY_STORE)
