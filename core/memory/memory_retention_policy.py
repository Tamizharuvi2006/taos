from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict


@dataclass
class MemoryPrivacySettings:
    memory_enabled: bool = True
    reference_chat_history: bool = True
    project_memory_enabled: bool = True
    shared_memory_enabled: bool = False
    auto_memory_suggestions: bool = True
    memory_trace_visibility: str = "summary"

    def update(self, **changes) -> "MemoryPrivacySettings":
        for key, value in changes.items():
            if value is not None and hasattr(self, key):
                setattr(self, key, value)
        if self.memory_trace_visibility not in {"summary", "off", "detailed"}:
            self.memory_trace_visibility = "summary"
        return self

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)
