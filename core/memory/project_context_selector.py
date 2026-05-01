from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .project_memory_model import ProjectMemoryRecord
from .project_memory_store import MultiChatProjectMemoryStore


@dataclass(frozen=True)
class SelectedProjectContext:
    record: ProjectMemoryRecord | None
    confidence: float
    stale: bool
    reason: str


def select_project_context(
    store: MultiChatProjectMemoryStore,
    *,
    user_id: str,
    query: str,
    project_id: str = "taos",
) -> SelectedProjectContext:
    lower = str(query or "").lower()
    record = store.get(user_id, project_id)
    if record is None:
        return SelectedProjectContext(None, 0.0, False, "No project memory found.")
    explicit = project_id in lower or "continue project" in lower or "continue taos" in lower
    stale = _is_stale(record.last_updated)
    confidence = 0.9 if explicit else 0.65
    if stale:
        confidence -= 0.2
    return SelectedProjectContext(record, max(0.0, confidence), stale, "Project query matched active project memory.")


def _is_stale(timestamp: str, *, days: int = 30) -> bool:
    try:
        dt = datetime.fromisoformat(timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days > days
    except Exception:
        return True
