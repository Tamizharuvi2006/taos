from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List

from .memory_policy import looks_like_secret
from .sensitive_memory_filter import check_sensitive_memory
from .user_memory_model import UserMemory


SECTION_TITLES = {
    "assistant_style": "Preferred assistant style",
    "user_identity_preference": "Identity preference",
    "user_preference": "Preferences",
    "career_goal": "Goals",
    "project_context": "Current project",
    "technical_stack": "Technical stack",
    "workflow_preference": "Workflow preferences",
    "active_task": "Active task",
    "long_term_memory": "Long-term memory",
    "project_memory": "Project memory",
    "saved_memory": "Saved memory",
    "chat_summary": "Chat summary memory",
}


def export_ai_profile(memories: Iterable[UserMemory], *, preferred_name: str = "") -> Dict[str, Any]:
    safe = [memory for memory in memories if _exportable(memory)]
    sections: Dict[str, List[str]] = defaultdict(list)
    for memory in safe:
        key = _section_for(memory)
        sections[key].append(memory.content)
    text = _render_profile(sections, preferred_name=preferred_name)
    return {
        "text": text,
        "json": {
            "profile_title": "User profile for AI assistant",
            "preferred_name": preferred_name,
            "sections": dict(sections),
            "do_not_store": [
                "Temporary one-off facts",
                "Secrets or API keys",
                "Passwords or tokens",
                "Sensitive personal data unless explicitly approved",
            ],
        },
        "memory_count": len(safe),
    }


def _exportable(memory: UserMemory) -> bool:
    if not memory.active or not memory.user_visible:
        return False
    if looks_like_secret(memory.content):
        return False
    sensitive = check_sensitive_memory(memory.content, explicit_user_request=False)
    return sensitive.allowed


def _section_for(memory: UserMemory) -> str:
    tags = {tag.lower() for tag in memory.tags}
    for tag in ("assistant_style", "user_preference", "career_goal", "project_context", "technical_stack", "active_task"):
        if tag in tags:
            return tag
    lower = memory.content.lower()
    if "tanglish" in lower or "mentor" in lower or "tone" in lower:
        return "assistant_style"
    if "preferred name" in lower or "call me" in lower or "user identity" in lower:
        return "user_identity_preference"
    if "goal" in lower or "job-ready" in lower or "career" in lower:
        return "career_goal"
    if "taos" in lower or "phase" in lower or memory.type == "project_memory":
        return "project_context"
    return memory.type


def _render_profile(sections: Dict[str, List[str]], *, preferred_name: str = "") -> str:
    lines = ["User profile for AI assistant:", ""]
    lines.append("Name / preferred name:")
    lines.append(f"- {preferred_name}" if preferred_name else "- Not specified")
    lines.append("")
    for key in (
        "user_identity_preference",
        "assistant_style",
        "career_goal",
        "project_context",
        "technical_stack",
        "workflow_preference",
        "active_task",
        "user_preference",
        "saved_memory",
        "project_memory",
        "chat_summary",
        "long_term_memory",
    ):
        values = sections.get(key) or []
        if not values:
            continue
        lines.append(f"{SECTION_TITLES.get(key, key.replace('_', ' ').title())}:")
        lines.extend(f"- {value}" for value in values)
        lines.append("")
    lines.append("Do not store:")
    lines.append("- Temporary one-off facts")
    lines.append("- Secrets or API keys")
    lines.append("- Passwords or tokens")
    return "\n".join(lines).strip() + "\n"
