from __future__ import annotations

from typing import Any, Dict

from .memory_exporter import export_ai_profile
from .user_memory_store import UserMemoryStore


def export_all_memory_bundle(store: UserMemoryStore, user_id: str) -> Dict[str, Any]:
    memories = [memory for memory in store.list(user_id) if memory.status == "active" and memory.user_visible]
    profile = export_ai_profile(memories)
    return {
        "user_id": user_id,
        "memory_count": len(memories),
        "memories": [memory.to_dict() for memory in memories],
        "ai_profile_text": profile["text"],
        "ai_profile_json": profile["json"],
    }
