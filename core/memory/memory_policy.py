from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Protocol


class TokenLike(Protocol):
    content: str


@dataclass(frozen=True)
class MemoryPolicy:
    """Conversation-memory window policy for Phase 140."""

    recent_turn_window: int = 10
    long_chat_recent_turn_window: int = 6
    full_context_token_limit: int = 6000
    summary_trigger_tokens: int = 8000
    summary_trigger_turns: int = 18
    max_summary_tokens: int = 1500
    max_project_facts: int = 40
    retrieval_top_k: int = 3

    def recent_message_limit(self, messages: Iterable[TokenLike]) -> int:
        rows = list(messages)
        total_tokens = estimate_tokens(" ".join(str(row.content or "") for row in rows))
        if total_tokens <= self.full_context_token_limit:
            return len(rows)
        turns = self.long_chat_recent_turn_window if total_tokens >= self.summary_trigger_tokens else self.recent_turn_window
        return max(2, turns * 2)

    def should_summarize(self, messages: Iterable[TokenLike]) -> bool:
        rows = list(messages)
        total_tokens = estimate_tokens(" ".join(str(row.content or "") for row in rows))
        return total_tokens >= self.summary_trigger_tokens or (len(rows) // 2) >= self.summary_trigger_turns


def estimate_tokens(text: str) -> int:
    return max(1, len(str(text or "")) // 4)


SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:api[_ -]?key|password|secret|token)\b\s*(?:is|=|:)\s*\S+", re.I),
)

TEMPORARY_HINTS = {
    "today",
    "tomorrow",
    "this evening",
    "for now",
    "temporary",
    "one time",
    "right now",
}

STABLE_HINTS = {
    "prefer",
    "preference",
    "always",
    "usually",
    "goal",
    "project",
    "roadmap",
    "phase",
    "decision",
    "architecture",
    "remember",
    "save",
    "note this",
}


@dataclass(frozen=True)
class MemorySaveDecision:
    should_save: bool
    reason: str
    tags: tuple[str, ...] = ()
    confidence: float = 0.0


def looks_like_secret(text: str) -> bool:
    value = str(text or "")
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)


def is_explicit_memory_request(text: str) -> bool:
    lower = str(text or "").lower()
    return any(token in lower for token in ("remember that", "remember this", "save this", "note this", "keep in memory"))


def is_forget_request(text: str) -> bool:
    lower = str(text or "").lower()
    return any(token in lower for token in ("forget ", "delete memory", "remove memory", "don't remember", "do not remember"))


def is_memory_question(text: str) -> bool:
    lower = str(text or "").lower()
    return "what do you remember" in lower or "show my memory" in lower or "what memory" in lower


def should_save_user_memory(text: str, *, explicit_sensitive_ok: bool = False) -> MemorySaveDecision:
    lower = str(text or "").lower()
    if looks_like_secret(lower):
        return MemorySaveDecision(False, "Blocked because the text looks like a secret/API key/password.", ("secret_blocked",), 0.99)
    if any(hint in lower for hint in TEMPORARY_HINTS) and not is_explicit_memory_request(lower):
        return MemorySaveDecision(False, "Temporary details are not saved automatically.", ("temporary",), 0.2)
    if is_explicit_memory_request(lower):
        return MemorySaveDecision(True, "User explicitly asked TAOS to remember this.", ("explicit",), 0.95)
    if any(hint in lower for hint in ("prefer", "usually", "always")):
        return MemorySaveDecision(True, "Stable user preference detected.", ("preference",), 0.82)
    if any(hint in lower for hint in ("phase", "roadmap", "architecture decision", "source-of-record", "current project")):
        return MemorySaveDecision(True, "Long-term project fact detected.", ("project",), 0.78)
    if explicit_sensitive_ok and "sensitive" in lower:
        return MemorySaveDecision(True, "Sensitive memory allowed only by explicit user request.", ("explicit_sensitive",), 0.7)
    return MemorySaveDecision(False, "No durable memory signal detected.", ("not_durable",), 0.1)
