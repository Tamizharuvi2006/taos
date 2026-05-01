from __future__ import annotations

from typing import Dict, List

from .memory_index import build_memory_index
from .memory_ranker import RankedMemory, rank_memories
from .recall_query_builder import build_recall_query
from .user_memory_store import UserMemoryStore


class MemoryRetriever:
    def __init__(self, store: UserMemoryStore) -> None:
        self.store = store

    def retrieve(self, user_id: str, user_text: str, *, top_k: int = 5) -> List[RankedMemory]:
        recall = build_recall_query(user_text)
        memories = self.store.list(user_id)
        items = build_memory_index(memories)
        ranked = rank_memories(items, recall.query, explicit_reference=recall.explicit_recall)
        used = ranked[:top_k]
        for row in used:
            self.store.update(user_id, row.item.id, last_used_at=None)
        return used

    def memory_used_summary(self, user_id: str, user_text: str, *, top_k: int = 5) -> List[Dict[str, object]]:
        return [
            {
                "id": row.item.id,
                "type": row.item.source_type,
                "content": row.item.content,
                "reason_used": row.reason,
                "score": row.score,
                "attribution": row.item.metadata,
            }
            for row in self.retrieve(user_id, user_text, top_k=top_k)
        ]
