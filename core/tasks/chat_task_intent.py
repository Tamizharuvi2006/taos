"""Chat-to-task conversion helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass


TASK_KEYWORDS = (
    "every",
    "daily",
    "hourly",
    "weekly",
    "track",
    "monitor",
    "remind",
    "after",
)
DELIVERY_PHRASES = ("send me", "email me", "mail me", "notify me")
DELIVERY_WITHOUT_SCHEDULE_INFO_HINTS = (
    "link",
    "linkedin",
    "url",
    "profile",
    "details",
    "about",
    "who is",
    "what is",
    "bio",
    "biography",
)
SCHEDULE_MARKERS = re.compile(
    r"\b(after|in|every|daily|hourly|weekly|tomorrow|next\s+(?:hour|day|week))\b",
    re.I,
)


@dataclass
class ChatTaskSuggestion:
    is_task_intent: bool
    trigger_type: str = "manual"
    interval_seconds: int = 0
    max_runs: int = 0
    run_immediately: bool = True
    task_name: str = ""
    normalized_goal: str = ""


def detect_chat_task_intent(query: str) -> ChatTaskSuggestion:
    text = (query or "").strip()
    lower = text.lower()

    if not text:
        return ChatTaskSuggestion(is_task_intent=False)

    has_delivery_phrase = any(phrase in lower for phrase in DELIVERY_PHRASES)
    has_schedule_marker = bool(SCHEDULE_MARKERS.search(lower))
    has_task_keyword = any(k in lower for k in TASK_KEYWORDS)

    if has_delivery_phrase and not has_schedule_marker:
        if any(hint in lower for hint in DELIVERY_WITHOUT_SCHEDULE_INFO_HINTS):
            return ChatTaskSuggestion(is_task_intent=False)
        if "remind" not in lower and "monitor" not in lower and "track" not in lower:
            return ChatTaskSuggestion(is_task_intent=False)

    if not has_task_keyword and not (has_delivery_phrase and has_schedule_marker):
        return ChatTaskSuggestion(is_task_intent=False)

    interval = _extract_interval_seconds(lower)
    one_time = _is_one_time_reminder(lower)
    delayed_only = _is_delayed_only_reminder(lower)
    trigger = "time_based" if interval > 0 else "manual"
    name = _derive_name(text)
    normalized = _normalize_goal(text)

    return ChatTaskSuggestion(
        is_task_intent=True,
        trigger_type=trigger,
        interval_seconds=interval,
        max_runs=1 if one_time and interval > 0 else 0,
        run_immediately=False if delayed_only and interval > 0 else True,
        task_name=name,
        normalized_goal=normalized,
    )


def _extract_interval_seconds(text: str) -> int:
    # Natural reminder phrases should not become one-off manual tasks.
    # "tomorrow" implies at least a daily cadence for monitoring/reminders.
    if "tomorrow" in text:
        return 86400
    # Support "after one min", "in 1 minute", etc.
    normalized = (
        text.replace("one", "1")
        .replace("two", "2")
        .replace("three", "3")
        .replace("four", "4")
        .replace("five", "5")
        .replace("an ", "1 ")
        .replace("a ", "1 ")
    )
    # Preferred pattern: explicit "after/in N unit"
    match_after = re.search(
        r"(after|in)\s+(\d+)\s*(sec|secs|second|seconds|min|mins|minute|minutes|hour|hours|day|days)\b",
        normalized,
    )
    if match_after:
        value = int(match_after.group(2))
        unit = match_after.group(3)
        factors = {
            "sec": 1,
            "secs": 1,
            "second": 1,
            "seconds": 1,
            "min": 60,
            "mins": 60,
            "minute": 60,
            "minutes": 60,
            "hour": 3600,
            "hours": 3600,
            "day": 86400,
            "days": 86400,
        }
        return value * factors.get(unit, 0)

    # Fallback pattern: tolerate noisy wording like "after after one min"
    match_loose = re.search(
        r"\b(\d+)\s*(sec|secs|second|seconds|min|mins|minute|minutes|hour|hours|day|days)\b",
        normalized,
    )
    if match_loose:
        value = int(match_loose.group(1))
        unit = match_loose.group(2)
        factors = {
            "sec": 1,
            "secs": 1,
            "second": 1,
            "seconds": 1,
            "min": 60,
            "mins": 60,
            "minute": 60,
            "minutes": 60,
            "hour": 3600,
            "hours": 3600,
            "day": 86400,
            "days": 86400,
        }
        return value * factors.get(unit, 0)

    if "hourly" in text or "every hour" in text:
        return 3600
    if "daily" in text or "every day" in text:
        return 86400
    if "weekly" in text or "every week" in text:
        return 604800

    match = re.search(r"every\s+(\d+)\s*(minute|minutes|hour|hours|day|days|week|weeks)\b", text)
    if not match:
        return 0
    value = int(match.group(1))
    unit = match.group(2)

    factors = {
        "minute": 60,
        "minutes": 60,
        "hour": 3600,
        "hours": 3600,
        "day": 86400,
        "days": 86400,
        "week": 604800,
        "weeks": 604800,
    }
    return value * factors.get(unit, 0)


def _derive_name(query: str) -> str:
    cleaned = query.strip().rstrip(".")
    lower = cleaned.lower()

    interval = _extract_interval_seconds(lower)
    if "gold" in lower and "remind" in lower:
        if interval == 60:
            return "Gold Price Follow-up (1 min)"
        if interval > 0:
            return f"Gold Price Follow-up ({interval}s)"
        return "Gold Price Reminder"

    if "gold" in lower:
        return "Gold Price Check"

    if "remind" in lower and interval > 0:
        return f"Reminder Follow-up ({interval}s)"

    if len(cleaned) <= 64:
        return cleaned
    return cleaned[:61] + "..."


def _normalize_goal(query: str) -> str:
    cleaned = query.strip()
    prefixes = (
        "track ",
        "monitor ",
        "remind me to ",
    )
    lower = cleaned.lower()
    for prefix in prefixes:
        if lower.startswith(prefix):
            return cleaned[len(prefix):].strip().capitalize()
    return cleaned


def _is_one_time_reminder(text: str) -> bool:
    if any(k in text for k in ("every ", "daily", "hourly", "weekly")):
        return False
    if "tomorrow" in text:
        return True
    return bool(
        re.search(
            r"(after|in)\s+(\d+)\s*(sec|secs|second|seconds|min|mins|minute|minutes|hour|hours|day|days)\b",
            text,
        )
    )


def _is_delayed_only_reminder(text: str) -> bool:
    """True when user clearly asked to send something after/in a duration."""
    if any(k in text for k in ("every ", "daily", "hourly", "weekly")):
        return False
    return bool(
        re.search(
            r"(after|in)\s+(\d+)\s*(sec|secs|second|seconds|min|mins|minute|minutes|hour|hours|day|days)\b",
            text,
        )
    )
