from __future__ import annotations

import re
from typing import Dict


class TaskIntentNormalizer:
    """Extracts task/action hints from messy user input."""

    TASK_RE = re.compile(
        r"\b(remind|reminder|schedule|send|email|run|execute|build|create|generate|write|implement|deploy|install|test|refactor)\b",
        re.I,
    )
    REMINDER_RE = re.compile(r"\b(remind|reminder|schedule)\b", re.I)
    TIME_RE = re.compile(r"\b(?P<hour>1[0-2]|0?[1-9])(?::(?P<minute>[0-5][0-9]))?\s*(?P<ampm>am|pm)?\b", re.I)

    def normalize(self, query: str, *, normalized_query: str = "") -> Dict[str, object]:
        raw = str(query or "")
        text = str(normalized_query or raw)
        if not (self.TASK_RE.search(text) or self.TASK_RE.search(raw)):
            return {}
        hints: Dict[str, object] = {"action": "task"}
        if self.REMINDER_RE.search(text) or self.REMINDER_RE.search(raw):
            hints["action"] = "reminder"
            hints["normalized_task"] = _normalize_reminder_text(text)
        if "tomorrow" in text.lower():
            hints["date_hint"] = "tomorrow"
        if "morning" in text.lower():
            hints["time_of_day"] = "morning"
        time_match = self.TIME_RE.search(text)
        if time_match:
            minute = time_match.group("minute") or "00"
            ampm = time_match.group("ampm") or ""
            hints["time_hint"] = f"{time_match.group('hour')}:{minute}{ampm}".strip()
        return hints


def _normalize_reminder_text(text: str) -> str:
    out = re.sub(r"\btomorrow\s+morning\s+(?=\d)", "tomorrow morning at ", text, flags=re.I)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def normalize_task_intent(query: str, *, normalized_query: str = "") -> Dict[str, object]:
    return TaskIntentNormalizer().normalize(query, normalized_query=normalized_query)
