from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from uuid import uuid4


MEMORY_TYPES = {"saved_memory", "project_memory", "chat_summary", "retrieved_context"}
MEMORY_STATUSES = {"active", "disabled", "deleted"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_memory_id() -> str:
    return f"mem_{uuid4().hex[:12]}"


@dataclass
class UserMemory:
    user_id: str
    content: str
    type: str = "saved_memory"
    id: str = field(default_factory=new_memory_id)
    source_chat_id: str = ""
    source_message_id: str = ""
    confidence: float = 0.85
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    last_used_at: str = ""
    status: str = "active"
    user_visible: bool = True
    tags: List[str] = field(default_factory=list)
    reason_saved: str = ""

    def validate(self) -> "UserMemory":
        if not self.user_id:
            raise ValueError("user_id is required")
        if not self.content.strip():
            raise ValueError("content is required")
        if self.type not in MEMORY_TYPES:
            raise ValueError(f"unsupported memory type: {self.type}")
        if self.status not in MEMORY_STATUSES:
            raise ValueError(f"unsupported memory status: {self.status}")
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        self.tags = [str(tag).strip() for tag in self.tags if str(tag).strip()]
        return self

    @property
    def active(self) -> bool:
        return self.status == "active"

    def mark_used(self) -> None:
        self.last_used_at = utc_now_iso()
        self.updated_at = self.updated_at or self.created_at

    def update(self, **changes: Any) -> "UserMemory":
        for key, value in changes.items():
            if value is None or not hasattr(self, key) or key in {"id", "user_id", "created_at"}:
                continue
            setattr(self, key, value)
        self.updated_at = utc_now_iso()
        return self.validate()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserMemory":
        return cls(**dict(data)).validate()


def visible_active(memories: Iterable[UserMemory]) -> List[UserMemory]:
    return [memory for memory in memories if memory.active and memory.user_visible]
