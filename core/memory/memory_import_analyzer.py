from __future__ import annotations

import re
from typing import List

from .import_review_model import ImportReviewItem, ImportReviewSession
from .memory_import_policy import evaluate_import_line


def analyze_memory_import(text: str, *, user_id: str) -> ImportReviewSession:
    raw = str(text or "").strip()
    items: List[ImportReviewItem] = []
    for line in _candidate_lines(raw):
        item = _analyze_line(line)
        if item is not None:
            items.append(item)
    if not items and raw:
        items.append(
            ImportReviewItem(
                type="long_term_memory",
                content=_clean(raw[:500]),
                confidence=0.45,
                recommended=False,
                editable=True,
                risk="medium",
                reason="Could not confidently classify; user review required.",
            ).validate()
        )
    blocked = sum(1 for item in items if item.risk == "blocked")
    recommended = sum(1 for item in items if item.recommended)
    session = ImportReviewSession(
        user_id=user_id,
        raw_text=raw,
        items=items,
        summary=f"Found {len(items)} possible memories, {blocked} sensitive item blocked, {recommended} recommended.",
    )
    return session


def _candidate_lines(text: str) -> List[str]:
    lines: List[str] = []
    for raw in re.split(r"[\n;]+", text):
        cleaned = _clean(re.sub(r"^[-*•\d.)\s]+", "", raw))
        if len(cleaned) >= 4:
            lines.append(cleaned)
    return lines


def _analyze_line(line: str) -> ImportReviewItem | None:
    lower = line.lower()
    policy = evaluate_import_line(line)
    if not policy.allowed and policy.risk == "blocked":
        return ImportReviewItem(
            type="blocked_sensitive",
            content="[REDACTED BLOCKED MEMORY]",
            confidence=0.99,
            recommended=False,
            editable=False,
            risk="blocked",
            reason=policy.reason,
        ).validate()
    if not policy.allowed and policy.risk == "sensitive":
        return ImportReviewItem(
            type="long_term_memory",
            content=_clean(line),
            confidence=0.72,
            recommended=False,
            selected=False,
            editable=True,
            risk="sensitive",
            reason=f"{policy.reason} Explicit confirmation is required before saving.",
        ).validate()

    memory_type = _classify(lower)
    risk = "low"
    recommended = memory_type not in {"temporary_context"}
    confidence = _confidence_for(memory_type, lower)
    if memory_type == "temporary_context":
        risk = "medium"
    return ImportReviewItem(
        type=memory_type,
        content=_normalize_content(line, memory_type),
        confidence=confidence,
        recommended=recommended,
        selected=recommended,
        editable=True,
        risk=risk,
        reason=f"Classified as {memory_type}.",
    ).validate()


def _classify(lower: str) -> str:
    if any(token in lower for token in ("my name is", "call me", "preferred name", "address me")):
        return "user_identity_preference"
    if any(token in lower for token in ("assistant style", "tone", "friendly", "mentor", "tanglish", "concise", "strict")):
        return "assistant_style"
    if any(token in lower for token in ("goal", "become", "career", "job-ready", "job ready", "learn")):
        return "career_goal"
    if any(token in lower for token in ("project", "taos", "phase", "roadmap", "architecture", "current focus")):
        return "project_context"
    if any(token in lower for token in ("react", "next.js", "python", "fastapi", "firebase", "stack", "technical")):
        return "technical_stack"
    if any(token in lower for token in ("codex prompt", "phase plan", "checklist", "test command", "step-by-step", "output preference")):
        return "workflow_preference"
    if any(token in lower for token in ("current task", "active task", "next step", "doing now")):
        return "active_task"
    if any(token in lower for token in ("prefer", "likes", "wants", "usually", "always")):
        return "long_term_preference"
    if any(token in lower for token in ("today", "tomorrow", "temporary", "for now")):
        return "temporary_context"
    return "long_term_memory"


def _confidence_for(memory_type: str, lower: str) -> float:
    if memory_type in {"user_identity_preference", "assistant_style", "career_goal", "project_context", "workflow_preference", "long_term_preference"}:
        return 0.9
    if memory_type == "technical_stack":
        return 0.82
    if memory_type == "active_task":
        return 0.72
    if memory_type == "temporary_context":
        return 0.4
    return 0.65


def _normalize_content(line: str, memory_type: str) -> str:
    cleaned = _clean(line)
    prefixes = {
        "assistant_style": "Assistant style: ",
        "career_goal": "User goal: ",
        "project_context": "Project context: ",
        "technical_stack": "Technical stack: ",
        "active_task": "Active task: ",
        "workflow_preference": "Workflow preference: ",
        "user_identity_preference": "User identity preference: ",
        "long_term_preference": "Long-term preference: ",
    }
    if any(cleaned.lower().startswith(prefix.lower().strip()) for prefix in prefixes.values()):
        return cleaned
    return f"{prefixes.get(memory_type, '')}{cleaned}".strip()


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip(" -:\t"))
