from __future__ import annotations

from collections import Counter
from typing import Dict, List

from .feedback_model import UserFeedback


class FeedbackStore:
    def __init__(self) -> None:
        self._rows: List[UserFeedback] = []

    def add(self, feedback: UserFeedback) -> str:
        feedback.validate()
        self._rows.append(feedback)
        return feedback.id

    def list(self, *, user_id: str | None = None) -> List[Dict[str, object]]:
        rows = [row for row in self._rows if user_id is None or row.user_id == user_id]
        return [row.to_dict() for row in rows]

    def summary(self) -> Dict[str, object]:
        counts = Counter(row.feedback_type for row in self._rows)
        route_counts = Counter(row.route or "unknown" for row in self._rows)
        return {
            "total": len(self._rows),
            "feedback_counts": dict(counts),
            "route_counts": dict(route_counts),
            "automatic_behavior_change": False,
        }


GLOBAL_FEEDBACK_STORE = FeedbackStore()
