from __future__ import annotations

from typing import Dict

from .user_memory_store import UserMemoryStore


class MemoryDeletionService:
    def __init__(self, store: UserMemoryStore) -> None:
        self.store = store

    def delete_all(self, user_id: str, *, hard: bool = False) -> Dict[str, int]:
        return {"deleted": self.store.delete_all(user_id, hard=hard)}

    def delete_imported(self, user_id: str, *, hard: bool = False) -> Dict[str, int]:
        count = 0
        for memory in list(self.store.list(user_id)):
            if "imported" in memory.tags:
                if self.store.delete(user_id, memory.id, hard=hard):
                    count += 1
        return {"deleted": count}

    def delete_project(self, user_id: str, project_id: str, *, hard: bool = False) -> Dict[str, int]:
        count = 0
        needle = str(project_id or "").lower()
        for memory in list(self.store.list(user_id)):
            if memory.type == "project_memory" and (needle in memory.content.lower() or needle in " ".join(memory.tags).lower()):
                if self.store.delete(user_id, memory.id, hard=hard):
                    count += 1
        return {"deleted": count}
