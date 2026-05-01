from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Protocol


class MessageLike(Protocol):
    content: str


def extract_project_state(messages: Iterable[MessageLike], *, project_name: str = "TAOS") -> Dict[str, Any]:
    completed: List[str] = []
    blockers: List[str] = []
    decisions: List[str] = []
    files: List[str] = []
    next_steps: List[str] = []
    current_phase = ""
    for message in messages:
        text = str(message.content or "")
        for phase in re.findall(r"\bphase\s+([0-9]{2,3}[a-z]?)\b", text, re.I):
            normalized = phase.upper()
            current_phase = f"Phase {normalized}"
            if re.search(rf"phase\s+{re.escape(phase)}[^.\n]{{0,120}}\b(done|complete|completed|passed|green)\b", text, re.I):
                _append(completed, normalized)
        if "source-of-record" in text.lower():
            _append(decisions, "Package-version source-of-record behavior is locked.")
        if "universal understanding" in text.lower():
            _append(decisions, "Universal understanding gateway runs before route decision.")
        for match in re.finditer(r"\b(?:blocker|warning|must fix later)\b[:\s-]*([^\n]{4,180})", text, re.I):
            _append(blockers, match.group(1).strip())
        for match in re.finditer(r"\b([a-zA-Z0-9_/\\.-]+\.(?:py|md|json|js|jsx|ts|tsx))\b", text):
            _append(files, match.group(1).replace("\\", "/"))
        for match in re.finditer(r"\bnext\s*:\s*([^\n]{4,160})", text, re.I):
            _append(next_steps, match.group(1).strip())
    return {
        "project_name": project_name,
        "current_phase": current_phase,
        "completed_phases": completed,
        "active_blockers": blockers,
        "important_decisions": decisions,
        "important_files": files,
        "next_steps": next_steps,
    }


def _append(values: List[str], value: str) -> None:
    cleaned = re.sub(r"\s+", " ", str(value or "").strip(" -:."))
    if cleaned and cleaned not in values:
        values.append(cleaned)
