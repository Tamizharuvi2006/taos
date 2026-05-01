from __future__ import annotations

from typing import Dict, List

from .memory_space_store import MemorySpaceStore
from .shared_memory_permissions import can_read


def shared_memory_for_context(
    store: MemorySpaceStore,
    *,
    user_id: str,
    active_space_id: str,
) -> Dict[str, object]:
    space = store.get_space(active_space_id, user_id)
    memories = [
        memory.to_dict()
        for memory in space.memories.values()
        if memory.status == "active" and can_read(space, user_id)
    ]
    return {
        "memory_space_used_summary": {
            "space_id": space.id,
            "scope": space.scope,
            "name": space.name,
            "memory_count": len(memories),
        },
        "shared_memories": memories,
    }
