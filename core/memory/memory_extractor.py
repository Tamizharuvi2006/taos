from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import List

from .memory_policy import is_explicit_memory_request, is_forget_request, should_save_user_memory
from .sensitive_memory_filter import check_sensitive_memory
from .user_memory_model import UserMemory


@dataclass(frozen=True)
class MemoryExtraction:
    action: str
    memories: List[UserMemory] = field(default_factory=list)
    blocked_reason: str = ""
    forget_query: str = ""


def extract_memories_from_text(
    text: str,
    *,
    user_id: str,
    source_chat_id: str = "",
    source_message_id: str = "",
) -> MemoryExtraction:
    value = str(text or "").strip()
    if not value:
        return MemoryExtraction(action="none")

    if is_forget_request(value):
        return MemoryExtraction(action="forget", forget_query=_forget_query(value))

    decision = should_save_user_memory(value)
    sensitive = check_sensitive_memory(value, explicit_user_request=is_explicit_memory_request(value))
    if not sensitive.allowed:
        return MemoryExtraction(action="blocked", blocked_reason=sensitive.reason)
    if not decision.should_save:
        return MemoryExtraction(action="none", blocked_reason=decision.reason)

    content = _memory_content(value)
    memory_type = "project_memory" if "phase" in value.lower() or "project" in value.lower() else "saved_memory"
    memory = UserMemory(
        user_id=user_id,
        type=memory_type,
        content=content,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        confidence=decision.confidence,
        tags=list(decision.tags),
        reason_saved=decision.reason,
    ).validate()
    return MemoryExtraction(action="save", memories=[memory])


def summarize_visible_memories(memories: List[UserMemory]) -> str:
    if not memories:
        return "I do not have any active user-visible memories saved for you."
    lines = ["I have these active memories:"]
    for memory in memories:
        lines.append(f"- {memory.content} ({memory.type}, {memory.status})")
    return "\n".join(lines)


def build_memory_used_summary(memories: List[UserMemory], *, reason_used: str = "Relevant to this answer") -> List[dict]:
    return [
        {
            "id": memory.id,
            "type": memory.type,
            "content": memory.content,
            "reason_used": reason_used,
        }
        for memory in memories
        if memory.active and memory.user_visible
    ]


def _memory_content(text: str) -> str:
    cleaned = re.sub(r"^\s*(remember that|remember this|save this|note this|keep in memory)\s*[:,-]?\s*", "", text, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip()


def _forget_query(text: str) -> str:
    cleaned = re.sub(r"^\s*(please\s+)?(forget|delete|remove)(\s+memory\s+about|\s+that|\s+this)?\s*", "", text, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip()
