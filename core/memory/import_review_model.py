from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List
from uuid import uuid4

from .user_memory_model import utc_now_iso


IMPORT_MEMORY_TYPES = {
    "user_identity_preference",
    "user_preference",
    "career_goal",
    "project_context",
    "assistant_style",
    "technical_stack",
    "workflow_preference",
    "active_task",
    "long_term_preference",
    "long_term_memory",
    "temporary_context",
    "blocked_sensitive",
}


def new_import_session_id() -> str:
    return f"imp_{uuid4().hex[:12]}"


def new_import_item_id() -> str:
    return f"item_{uuid4().hex[:10]}"


@dataclass
class ImportReviewItem:
    type: str
    content: str
    confidence: float
    recommended: bool
    editable: bool
    risk: str
    id: str = field(default_factory=new_import_item_id)
    selected: bool = False
    reason: str = ""

    def validate(self) -> "ImportReviewItem":
        if self.type not in IMPORT_MEMORY_TYPES:
            raise ValueError(f"unsupported import memory type: {self.type}")
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        if self.type == "blocked_sensitive":
            self.recommended = False
            self.selected = False
            self.editable = False
            self.risk = "blocked"
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ImportReviewSession:
    user_id: str
    raw_text: str
    items: List[ImportReviewItem]
    id: str = field(default_factory=new_import_session_id)
    created_at: str = field(default_factory=utc_now_iso)
    status: str = "pending"
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "import_session_id": self.id,
            "user_id": self.user_id,
            "summary": self.summary,
            "status": self.status,
            "created_at": self.created_at,
            "items": [item.to_dict() for item in self.items],
        }
