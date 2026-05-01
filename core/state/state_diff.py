"""
TAOS State Diff Tracking — Computes and formats differences between states.

Used for:
- Observability: log what changed at each step
- Debugging: replay and inspect state transitions
- Integrity: detect unexpected mutations
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from taos.core.state.state_schema import GlobalState


@dataclass
class FieldChange:
    """A single field-level change."""

    field: str
    old_value: Any
    new_value: Any

    def __str__(self) -> str:
        return f"{self.field}: {self.old_value!r} → {self.new_value!r}"


@dataclass
class StateDiff:
    """
    Complete diff between two state versions.
    
    Captures every field that changed, the version transition,
    and provides formatted output for logging.
    """

    from_version: int
    to_version: int
    changes: List[FieldChange] = field(default_factory=list)
    timestamp: float = 0.0

    @property
    def has_changes(self) -> bool:
        return len(self.changes) > 0

    @property
    def changed_fields(self) -> List[str]:
        return [c.field for c in self.changes]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_version": self.from_version,
            "to_version": self.to_version,
            "changes": [
                {"field": c.field, "old": _safe_repr(c.old_value), "new": _safe_repr(c.new_value)}
                for c in self.changes
            ],
        }

    def to_log_string(self) -> str:
        """Format for structured logging."""
        if not self.changes:
            return f"State v{self.from_version} → v{self.to_version}: no changes"
        lines = [f"State v{self.from_version} → v{self.to_version}:"]
        for c in self.changes:
            lines.append(f"  Δ {c}")
        return "\n".join(lines)


def compute_diff(prev: GlobalState, new: GlobalState) -> StateDiff:
    """
    Compute the diff between two GlobalState instances.
    
    Compares all fields except internal versioning fields.
    Returns a StateDiff with all changed fields.
    """
    # Fields to skip (they always change on version bump)
    skip_fields = {"state_version", "prev_state_hash", "updated_at"}

    changes: List[FieldChange] = []

    prev_dict = prev.model_dump()
    new_dict = new.model_dump()

    for key in prev_dict:
        if key in skip_fields:
            continue

        old_val = prev_dict[key]
        new_val = new_dict.get(key)

        if old_val != new_val:
            changes.append(FieldChange(field=key, old_value=old_val, new_value=new_val))

    return StateDiff(
        from_version=prev.state_version,
        to_version=new.state_version,
        changes=changes,
        timestamp=new.updated_at,
    )


def _safe_repr(value: Any) -> Any:
    """Safe representation for logging (truncate large values)."""
    if isinstance(value, str) and len(value) > 200:
        return value[:200] + "..."
    if isinstance(value, list) and len(value) > 10:
        return f"[{len(value)} items]"
    if isinstance(value, dict) and len(str(value)) > 200:
        return f"{{...{len(value)} keys}}"
    return value
