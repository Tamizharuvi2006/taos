from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Dict, Iterable, List, Protocol

from .memory_policy import estimate_tokens


class MessageLike(Protocol):
    id: str
    role: str
    content: str


@dataclass
class RollingSummary:
    current_goal: str = ""
    completed_phases: List[str] = field(default_factory=list)
    important_decisions: List[str] = field(default_factory=list)
    open_blockers: List[str] = field(default_factory=list)
    user_preferences: List[str] = field(default_factory=list)
    code_files_touched: List[str] = field(default_factory=list)
    next_action: str = ""
    evidence_message_ids: List[str] = field(default_factory=list)
    version: int = 0

    def update_from_messages(self, messages: Iterable[MessageLike], *, max_tokens: int = 1500) -> "RollingSummary":
        for message in messages:
            content = str(message.content or "")
            self._merge(message_id=message.id, facts=extract_summary_facts(content, role=message.role))
        self._cap(max_tokens=max_tokens)
        self.version += 1
        return self

    def as_dict(self) -> Dict[str, object]:
        return {
            "current_goal": self.current_goal,
            "completed_phases": list(self.completed_phases),
            "important_decisions": list(self.important_decisions),
            "open_blockers": list(self.open_blockers),
            "user_preferences": list(self.user_preferences),
            "code_files_touched": list(self.code_files_touched),
            "next_action": self.next_action,
            "evidence_message_ids": list(self.evidence_message_ids),
            "version": self.version,
        }

    def as_text(self) -> str:
        sections = [
            ("Current goal", [self.current_goal] if self.current_goal else []),
            ("Completed phases", self.completed_phases),
            ("Important decisions", self.important_decisions),
            ("Open blockers", self.open_blockers),
            ("User preferences", self.user_preferences),
            ("Code files touched", self.code_files_touched),
            ("Next action", [self.next_action] if self.next_action else []),
        ]
        lines: List[str] = []
        for title, values in sections:
            if not values:
                continue
            lines.append(f"{title}:")
            lines.extend(f"- {value}" for value in values)
        return "\n".join(lines).strip()

    def _merge(self, *, message_id: str, facts: Dict[str, List[str]]) -> None:
        if any(facts.values()) and message_id not in self.evidence_message_ids:
            self.evidence_message_ids.append(message_id)
        if facts.get("current_goal"):
            self.current_goal = facts["current_goal"][-1]
        if facts.get("next_action"):
            self.next_action = facts["next_action"][-1]
        for key, attr in (
            ("completed_phases", "completed_phases"),
            ("important_decisions", "important_decisions"),
            ("open_blockers", "open_blockers"),
            ("user_preferences", "user_preferences"),
            ("code_files_touched", "code_files_touched"),
        ):
            current = getattr(self, attr)
            for value in facts.get(key, []):
                _append_unique(current, value)

    def _cap(self, *, max_tokens: int) -> None:
        while estimate_tokens(self.as_text()) > max_tokens and self.important_decisions:
            self.important_decisions.pop(0)
        for attr in ("completed_phases", "open_blockers", "user_preferences", "code_files_touched"):
            values = getattr(self, attr)
            del values[:-20]


def extract_summary_facts(content: str, *, role: str = "") -> Dict[str, List[str]]:
    text = str(content or "")
    lower = text.lower()
    facts: Dict[str, List[str]] = {
        "current_goal": [],
        "completed_phases": [],
        "important_decisions": [],
        "open_blockers": [],
        "user_preferences": [],
        "code_files_touched": [],
        "next_action": [],
    }
    for match in re.finditer(r"\bphase\s+([0-9]{2,3}[a-z]?)\b[^.\n]{0,80}\b(done|complete|completed|passed|green)\b", text, re.I):
        facts["completed_phases"].append(f"Phase {match.group(1).upper()} {match.group(2).lower()}")
    for match in re.finditer(r"\b(?:current goal|goal|next)\s*:\s*([^\n]{6,160})", text, re.I):
        key = "next_action" if match.group(0).lower().startswith("next") else "current_goal"
        facts[key].append(_clean(match.group(1)))
    if "package-version" in lower and ("locked" in lower or "source-of-record" in lower):
        facts["important_decisions"].append("Package-version source-of-record behavior is locked.")
    if "best-supported" in lower and "generic" in lower:
        facts["important_decisions"].append("Research answers should give best-supported status instead of generic no-result wording.")
    if "public-only" in lower or "do not bypass login" in lower:
        facts["important_decisions"].append("Entity/profile intelligence uses public evidence only and must not bypass login.")
    if "development_log.md" in lower or "dev log" in lower:
        facts["user_preferences"].append("Phase-based work should update DEVELOPMENT_LOG.md.")
    if "tanglish" in lower or "da" in lower:
        facts["user_preferences"].append("User is comfortable with casual Tanglish-style collaboration.")
    for match in re.finditer(r"\b(?:blocker|warning|not urgent|must fix later)\b[:\s-]*([^\n]{5,180})", text, re.I):
        facts["open_blockers"].append(_clean(match.group(1)))
    for match in re.finditer(r"\b([a-zA-Z0-9_/\\.-]+\.(?:py|md|json|js|jsx|ts|tsx))\b", text):
        facts["code_files_touched"].append(match.group(1).replace("\\", "/"))
    return facts


def _append_unique(values: List[str], value: str) -> None:
    text = _clean(value)
    if text and text not in values:
        values.append(text)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip(" -:.")).strip()
