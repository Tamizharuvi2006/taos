from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict
from uuid import uuid4


ALLOWED_FEEDBACK_TYPES = {
    "wrong_answer",
    "bad_sources",
    "didnt_understand",
    "too_vague",
    "too_slow",
    "good_answer",
}


@dataclass(frozen=True)
class UserFeedback:
    query: str
    feedback_type: str
    answer: str = ""
    user_id: str = "default"
    route: str = ""
    answer_mode: str = ""
    source_quality: float = 0.0
    notes: str = ""
    id: str = field(default_factory=lambda: f"uf_{uuid4().hex[:10]}")
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    def validate(self) -> None:
        if self.feedback_type not in ALLOWED_FEEDBACK_TYPES:
            raise ValueError(f"Unsupported feedback_type: {self.feedback_type}")
        if not self.query.strip():
            raise ValueError("query is required")
