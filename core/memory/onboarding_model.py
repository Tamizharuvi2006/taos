from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from .import_review_model import ImportReviewItem
from .user_memory_model import utc_now_iso


@dataclass
class OnboardingAnswers:
    preferred_name: str = ""
    assistant_style: str = ""
    language_tone: str = ""
    goals: str = ""
    technical_stack: str = ""
    active_projects: str = ""
    current_blockers: str = ""
    output_preferences: str = ""
    boundaries: str = ""


@dataclass
class OnboardingSession:
    user_id: str
    items: List[ImportReviewItem]
    status: str = "pending"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass
class OnboardingStatus:
    user_id: str
    status: str = "incomplete"
    completed_at: str = ""
    skipped_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
