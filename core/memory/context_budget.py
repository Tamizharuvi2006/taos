from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .memory_policy import estimate_tokens


@dataclass(frozen=True)
class ContextBudget:
    max_context_tokens: int = 32000
    system_tokens: int = 3000
    recent_raw_tokens: int = 8000
    rolling_summary_tokens: int = 3000
    memories_tokens: int = 2000
    retrieved_tokens: int = 6000
    tool_context_tokens: int = 8000
    buffer_tokens: int = 2000

    def remaining(self, used_tokens: int) -> int:
        return max(0, self.max_context_tokens - int(used_tokens or 0) - self.buffer_tokens)

    def within(self, text: str, limit: int) -> bool:
        return estimate_tokens(text) <= max(0, int(limit or 0))

    def as_dict(self) -> Dict[str, int]:
        return {
            "max_context_tokens": self.max_context_tokens,
            "system_tokens": self.system_tokens,
            "recent_raw_tokens": self.recent_raw_tokens,
            "rolling_summary_tokens": self.rolling_summary_tokens,
            "memories_tokens": self.memories_tokens,
            "retrieved_tokens": self.retrieved_tokens,
            "tool_context_tokens": self.tool_context_tokens,
            "buffer_tokens": self.buffer_tokens,
        }
