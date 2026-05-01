from __future__ import annotations

from typing import Iterable, List, Protocol

from .memory_policy import estimate_tokens


class MessageLike(Protocol):
    content: str


def select_recent_messages(
    messages: Iterable[MessageLike],
    *,
    full_context_token_limit: int = 6000,
    normal_turns: int = 10,
    long_chat_turns: int = 6,
    long_chat_token_threshold: int = 8000,
) -> List[MessageLike]:
    rows = list(messages)
    total_tokens = estimate_tokens(" ".join(str(row.content or "") for row in rows))
    if total_tokens <= full_context_token_limit:
        return rows
    turns = long_chat_turns if total_tokens >= long_chat_token_threshold else normal_turns
    return rows[-max(2, turns * 2):]
