from __future__ import annotations

from threading import RLock
from typing import Dict, List

from .project_memory_model import ProjectMemoryRecord


class MultiChatProjectMemoryStore:
    def __init__(self) -> None:
        self._records: Dict[tuple[str, str], ProjectMemoryRecord] = {}
        self._lock = RLock()

    def upsert(self, record: ProjectMemoryRecord) -> ProjectMemoryRecord:
        key = (record.user_id, record.project_id)
        with self._lock:
            existing = self._records.get(key)
            if existing is None:
                self._records[key] = record
                return record
            existing.merge(record)
            return existing

    def get(self, user_id: str, project_id: str) -> ProjectMemoryRecord | None:
        with self._lock:
            return self._records.get((user_id, project_id))

    def list(self, user_id: str) -> List[ProjectMemoryRecord]:
        with self._lock:
            return [record for (uid, _), record in self._records.items() if uid == user_id]

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


GLOBAL_PROJECT_MEMORY_STORE = MultiChatProjectMemoryStore()
