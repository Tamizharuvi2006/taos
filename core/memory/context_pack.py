from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List

from .context_budget import ContextBudget
from .conversation_memory_manager import ConversationMessage
from .conversation_window import select_recent_messages
from .memory_extractor import build_memory_used_summary
from .project_state_extractor import extract_project_state
from .rolling_summary import RollingSummary
from .user_memory_model import UserMemory
from .user_memory_store import UserMemoryStore


@dataclass
class ContextPack:
    current_user_message: str
    recent_messages: List[Dict[str, Any]]
    rolling_summary: Dict[str, Any]
    project_state: Dict[str, Any]
    saved_memories: List[Dict[str, Any]]
    retrieved_snippets: List[Dict[str, Any]] = field(default_factory=list)
    trace: Dict[str, Any] = field(default_factory=dict)


class ContextPackBuilder:
    def __init__(self, *, budget: ContextBudget | None = None, memory_store: UserMemoryStore | None = None) -> None:
        self.budget = budget or ContextBudget()
        self.memory_store = memory_store

    def build(
        self,
        *,
        user_id: str,
        current_user_message: str,
        messages: Iterable[ConversationMessage],
        rolling_summary: RollingSummary | None = None,
        saved_memories: Iterable[UserMemory] | None = None,
        retrieved_snippets: Iterable[Dict[str, Any]] | None = None,
    ) -> ContextPack:
        rows = list(messages)
        recent = select_recent_messages(rows)
        summary = rolling_summary or RollingSummary()
        memories = list(saved_memories or [])
        if self.memory_store is not None:
            memories = self.memory_store.retrieve(user_id, current_user_message, top_k=5)
        active_memories = [memory for memory in memories if memory.active and memory.user_visible]
        snippets = list(retrieved_snippets or [])
        trace = {
            "context_summary": {
                "recent_turns_count": len(recent) // 2,
                "rolling_summary_used": bool(summary.as_text()),
                "memories_used_count": len(active_memories),
                "retrieved_snippets_count": len(snippets),
                "truncated_reason": "" if len(recent) == len(rows) else "conversation_window_budget",
                "budget": self.budget.as_dict(),
            },
            "memory_used_summary": build_memory_used_summary(active_memories),
        }
        return ContextPack(
            current_user_message=current_user_message,
            recent_messages=[asdict(message) for message in recent],
            rolling_summary=summary.as_dict(),
            project_state=extract_project_state(rows),
            saved_memories=[memory.to_dict() for memory in active_memories],
            retrieved_snippets=snippets,
            trace=trace,
        )
