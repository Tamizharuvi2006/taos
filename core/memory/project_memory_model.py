from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProjectMemoryRecord:
    project_id: str
    project_name: str
    user_id: str
    current_phase: str = ""
    completed_phases: List[str] = field(default_factory=list)
    active_blockers: List[str] = field(default_factory=list)
    important_decisions: List[str] = field(default_factory=list)
    important_files: List[str] = field(default_factory=list)
    pending_tests: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    last_updated: str = field(default_factory=now_iso)

    def merge(self, other: "ProjectMemoryRecord") -> "ProjectMemoryRecord":
        if other.current_phase:
            self.current_phase = other.current_phase
        for attr in ("completed_phases", "active_blockers", "important_decisions", "important_files", "pending_tests", "next_steps"):
            target = getattr(self, attr)
            for value in getattr(other, attr):
                if value and value not in target:
                    target.append(value)
        self.last_updated = now_iso()
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
