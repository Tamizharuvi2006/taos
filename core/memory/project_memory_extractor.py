from __future__ import annotations

import re
from typing import List

from .project_memory_model import ProjectMemoryRecord


def extract_project_memory(text: str, *, user_id: str, default_project_id: str = "taos") -> ProjectMemoryRecord | None:
    value = str(text or "")
    lower = value.lower()
    if default_project_id not in lower and "phase" not in lower and "project" not in lower:
        return None
    project_id = "taos" if "taos" in lower or default_project_id == "taos" else default_project_id
    project_name = "TAOS AgentOS" if project_id == "taos" else project_id
    completed: List[str] = []
    current_phase = ""
    fallback_phase = ""
    for match in re.finditer(r"\bphase\s+([0-9]{2,3}[a-z]?)\b", value, re.I):
        phase = match.group(1).upper()
        fallback_phase = f"Phase {phase}"
        if re.search(rf"phase\s+{re.escape(match.group(1))}[^.\n]{{0,120}}\b(done|complete|completed|passed|green)\b", value, re.I):
            completed.append(phase)
            if not current_phase:
                current_phase = f"Phase {phase}"
    if not current_phase:
        current_phase = fallback_phase
    decisions = []
    if "source-of-record" in lower:
        decisions.append("Package-version lookups use the locked source-of-record path.")
    if "universal understanding" in lower:
        decisions.append("Universal understanding gateway runs before routing.")
    next_steps = [m.strip() for m in re.findall(r"\bnext\s*:\s*([^\n]{4,160})", value, re.I)]
    blockers = [m.strip() for m in re.findall(r"\bblocker\s*:\s*([^\n]{4,160})", value, re.I)]
    tests = [m.strip() for m in re.findall(r"\b(?:test|run)\s*:\s*([^\n]{4,160})", value, re.I)]
    files = [m.replace("\\", "/") for m in re.findall(r"\b([a-zA-Z0-9_/\\.-]+\.(?:py|md|json|js|jsx|ts|tsx))\b", value)]
    return ProjectMemoryRecord(
        project_id=project_id,
        project_name=project_name,
        user_id=user_id,
        current_phase=current_phase,
        completed_phases=completed,
        active_blockers=blockers,
        important_decisions=decisions,
        important_files=files,
        pending_tests=tests,
        next_steps=next_steps,
    )
