from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from .user_memory_model import UserMemory


@dataclass(frozen=True)
class MemoryIndexItem:
    id: str
    user_id: str
    source_type: str
    content: str
    confidence: float = 0.8
    metadata: Dict[str, object] = field(default_factory=dict)


def build_memory_index(memories: Iterable[UserMemory]) -> List[MemoryIndexItem]:
    items: List[MemoryIndexItem] = []
    for memory in memories:
        if not memory.active or not memory.user_visible:
            continue
        items.append(
            MemoryIndexItem(
                id=memory.id,
                user_id=memory.user_id,
                source_type=memory.type,
                content=memory.content,
                confidence=memory.confidence,
                metadata={
                    "tags": list(memory.tags),
                    "source_chat_id": memory.source_chat_id,
                    "source_message_id": memory.source_message_id,
                    "reason_saved": memory.reason_saved,
                },
            )
        )
    return items
