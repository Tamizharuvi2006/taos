from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import time
from typing import Any, Dict, Iterable, List

from .memory_policy import MemoryPolicy, estimate_tokens
from .project_memory import ProjectMemory
from .rolling_summary import RollingSummary


@dataclass(frozen=True)
class ConversationMessage:
    id: str
    role: str
    content: str
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def approx_tokens(self) -> int:
        return estimate_tokens(self.content)


@dataclass(frozen=True)
class RetrievedMemory:
    memory_id: str
    source_message_id: str
    snippet: str
    score: float
    reason: str


class ConversationMemoryManager:
    """Phase 140 chat-memory coordinator: raw window + summary + project facts + retrieval."""

    def __init__(
        self,
        *,
        policy: MemoryPolicy | None = None,
        rolling_summary: RollingSummary | None = None,
        project_memory: ProjectMemory | None = None,
    ) -> None:
        self.policy = policy or MemoryPolicy()
        self.rolling_summary = rolling_summary or RollingSummary()
        self.project_memory = project_memory or ProjectMemory(max_facts=self.policy.max_project_facts)
        self._messages: List[ConversationMessage] = []
        self._summarized_message_ids: set[str] = set()

    @property
    def messages(self) -> List[ConversationMessage]:
        return list(self._messages)

    def add_message(self, role: str, content: str, metadata: Dict[str, Any] | None = None) -> ConversationMessage:
        message = ConversationMessage(
            id=f"m{len(self._messages) + 1}",
            role=str(role or "user"),
            content=str(content or ""),
            metadata=dict(metadata or {}),
        )
        self._messages.append(message)
        self._refresh_memory_state()
        return message

    def add_turn(self, user: str, assistant: str = "") -> None:
        self.add_message("user", user)
        if assistant:
            self.add_message("assistant", assistant)

    def build_context(self, current_user_message: str = "", *, retrieval_query: str = "") -> Dict[str, Any]:
        recent = self.recent_raw_messages()
        retrieved = self.retrieve(retrieval_query or current_user_message, top_k=self.policy.retrieval_top_k)
        trace = {
            "recent_raw_message_count": len(recent),
            "rolling_summary_version": self.rolling_summary.version,
            "project_fact_count": len(self.project_memory.facts()),
            "retrieved_memory_count": len(retrieved),
            "retrieved_citations": [asdict(item) for item in retrieved],
            "memory_layers": ["recent_raw", "rolling_summary", "project_memory", "retrieval_memory"],
        }
        return {
            "current_user_message": current_user_message,
            "recent_messages": [asdict(message) for message in recent],
            "rolling_summary": self.rolling_summary.as_dict(),
            "project_memory": self.project_memory.as_trace(),
            "retrieved_memories": [asdict(item) for item in retrieved],
            "trace": {"memory_used": trace},
        }

    def recent_raw_messages(self) -> List[ConversationMessage]:
        limit = self.policy.recent_message_limit(self._messages)
        return self._messages[-limit:] if limit else []

    def retrieve(self, query: str, *, top_k: int = 3) -> List[RetrievedMemory]:
        query_terms = _terms(query)
        if not query_terms:
            return []
        recent_ids = {message.id for message in self.recent_raw_messages()}
        scored: List[RetrievedMemory] = []
        for message in self._messages:
            if message.id in recent_ids:
                continue
            terms = _terms(message.content)
            overlap = len(query_terms & terms)
            if overlap <= 0:
                continue
            score = round(overlap / max(1, len(query_terms | terms)), 3)
            scored.append(
                RetrievedMemory(
                    memory_id=f"conversation:{message.id}",
                    source_message_id=message.id,
                    snippet=_snippet(message.content),
                    score=score,
                    reason=f"lexical_overlap:{overlap}",
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]

    def answer_memory_status(self) -> str:
        return (
            "I keep the most recent messages exactly, summarize older parts, "
            "store stable project facts separately, and retrieve relevant older details when needed. "
            "I do not keep every old message in full context forever."
        )

    def _refresh_memory_state(self) -> None:
        self.project_memory.update_from_messages(self._messages[-2:])
        if not self.policy.should_summarize(self._messages):
            return
        recent_ids = {message.id for message in self.recent_raw_messages()}
        unsummarized = [
            message
            for message in self._messages
            if message.id not in recent_ids and message.id not in self._summarized_message_ids
        ]
        if not unsummarized:
            return
        self.rolling_summary.update_from_messages(unsummarized, max_tokens=self.policy.max_summary_tokens)
        self.project_memory.update_from_messages(unsummarized)
        self._summarized_message_ids.update(message.id for message in unsummarized)


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9_]+", str(text or "").lower()) if len(term) >= 3}


def _snippet(text: str, limit: int = 240) -> str:
    clean = re.sub(r"\s+", " ", str(text or "").strip())
    return clean[:limit]
