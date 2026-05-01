from __future__ import annotations

from typing import Dict

from .user_memory_model import UserMemory


def attribution_for_memory(memory: UserMemory) -> Dict[str, object]:
    return {
        "memory_id": memory.id,
        "source_chat_id": memory.source_chat_id,
        "source_message_id": memory.source_message_id,
        "reason_saved": memory.reason_saved,
        "confidence": memory.confidence,
    }


def cautious_memory_phrase(memory: UserMemory) -> str:
    if memory.confidence < 0.65:
        return f"I have a low-confidence saved memory that says: {memory.content}"
    return f"I have a saved memory that says: {memory.content}"
