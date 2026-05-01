from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict
from uuid import uuid4

from .user_memory_model import utc_now_iso


CONFLICT_TYPES = {
    "preference_conflict",
    "tone_conflict",
    "goal_conflict",
    "project_conflict",
    "identity_conflict",
    "stale_memory",
    "duplicate_memory",
    "sensitive_conflict",
}

RESOLUTION_ACTIONS = {"keep_old", "keep_new", "merge", "edit_manually", "disable_both", "mark_old_stale"}


def new_conflict_id() -> str:
    return f"conf_{uuid4().hex[:12]}"


@dataclass
class MemoryConflict:
    user_id: str
    type: str
    old_memory_id: str
    new_memory_id: str
    old_content: str
    new_content: str
    suggested_resolution: str = ""
    id: str = field(default_factory=new_conflict_id)
    status: str = "unresolved"
    created_at: str = field(default_factory=utc_now_iso)
    resolved_at: str = ""

    def validate(self) -> "MemoryConflict":
        if self.type not in CONFLICT_TYPES:
            raise ValueError(f"unsupported conflict type: {self.type}")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
