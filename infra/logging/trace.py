"""
TAOS Trace — Step-level execution tracing.

Captures a detailed trace of every step in execution for
debugging, replay, and observability.

Each trace entry contains:
- Step metadata (id, tool, action)
- Input/output data
- Timing information
- State transitions
- Errors and retries
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TraceEntry:
    """A single trace entry for one execution step."""

    step_id: str
    step_index: int
    action: str
    tool: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None

    # Timing
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    latency_ms: float = 0.0

    # State
    fsm_state_before: str = ""
    fsm_state_after: str = ""
    state_version_before: int = 0
    state_version_after: int = 0

    # Result
    success: bool = False
    result_summary: str = ""
    error: Optional[str] = None
    error_type: Optional[str] = None
    retries_used: int = 0

    # Reflection
    confidence: Optional[float] = None
    reflection_reasoning: str = ""

    # Cost
    step_cost: float = 0.0
    cumulative_cost: float = 0.0

    def complete(
        self,
        success: bool,
        result_summary: str = "",
        error: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> None:
        """Mark this trace entry as complete."""
        self.end_time = time.time()
        self.latency_ms = (self.end_time - self.start_time) * 1000
        self.success = success
        self.result_summary = result_summary[:500]
        self.error = error
        self.error_type = error_type

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for API response / storage."""
        return {
            "step_id": self.step_id,
            "step_index": self.step_index,
            "action": self.action,
            "tool": self.tool,
            "latency_ms": round(self.latency_ms, 2),
            "success": self.success,
            "result_summary": self.result_summary,
            "error": self.error,
            "error_type": self.error_type,
            "retries_used": self.retries_used,
            "confidence": self.confidence,
            "step_cost": self.step_cost,
            "cumulative_cost": self.cumulative_cost,
            "fsm_transition": f"{self.fsm_state_before} → {self.fsm_state_after}",
        }


class ExecutionTrace:
    """
    Captures the full execution trace of an agent task.

    Stores ordered TraceEntry objects and provides
    query/replay capabilities.
    """

    def __init__(self, request_id: str = "") -> None:
        self._request_id = request_id
        self._entries: List[TraceEntry] = []
        self._start_time = time.time()
        self._metadata: Dict[str, Any] = {}

    def start_step(
        self,
        step_id: str,
        step_index: int,
        action: str,
        tool: Optional[str] = None,
        tool_input: Optional[Dict[str, Any]] = None,
        fsm_state: str = "",
        state_version: int = 0,
    ) -> TraceEntry:
        """Begin tracing a new step. Returns the TraceEntry to fill in."""
        entry = TraceEntry(
            step_id=step_id,
            step_index=step_index,
            action=action,
            tool=tool,
            tool_input=tool_input,
            fsm_state_before=fsm_state,
            state_version_before=state_version,
        )
        self._entries.append(entry)
        return entry

    def complete_step(
        self,
        entry: TraceEntry,
        success: bool,
        result_summary: str = "",
        error: Optional[str] = None,
        error_type: Optional[str] = None,
        fsm_state_after: str = "",
        state_version_after: int = 0,
        confidence: Optional[float] = None,
        step_cost: float = 0.0,
        cumulative_cost: float = 0.0,
    ) -> None:
        """Complete a trace entry with results."""
        entry.complete(success, result_summary, error, error_type)
        entry.fsm_state_after = fsm_state_after
        entry.state_version_after = state_version_after
        entry.confidence = confidence
        entry.step_cost = step_cost
        entry.cumulative_cost = cumulative_cost

    def set_metadata(self, key: str, value: Any) -> None:
        """Set trace-level metadata."""
        self._metadata[key] = value

    # ─── Queries ──────────────────────────────────────────

    @property
    def entries(self) -> List[TraceEntry]:
        return list(self._entries)

    @property
    def step_count(self) -> int:
        return len(self._entries)

    @property
    def success_count(self) -> int:
        return sum(1 for e in self._entries if e.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for e in self._entries if not e.success)

    @property
    def total_latency_ms(self) -> float:
        return sum(e.latency_ms for e in self._entries)

    @property
    def total_cost(self) -> float:
        return sum(e.step_cost for e in self._entries)

    def get_failures(self) -> List[TraceEntry]:
        """Get all failed trace entries."""
        return [e for e in self._entries if not e.success]

    def get_by_tool(self, tool_name: str) -> List[TraceEntry]:
        """Get trace entries for a specific tool."""
        return [e for e in self._entries if e.tool == tool_name]

    # ─── Serialization ────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the full trace for API response."""
        elapsed = time.time() - self._start_time
        return {
            "request_id": self._request_id,
            "step_count": self.step_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "total_cost": self.total_cost,
            "elapsed_seconds": round(elapsed, 2),
            "metadata": self._metadata,
            "steps": [e.to_dict() for e in self._entries],
        }

    def to_timeline(self) -> str:
        """Human-readable timeline for debugging."""
        lines = [f"═══ Trace: {self._request_id} ═══"]
        for entry in self._entries:
            status = "✓" if entry.success else "✗"
            tool_str = f" [{entry.tool}]" if entry.tool else ""
            lines.append(
                f"  {status} Step {entry.step_index}{tool_str}: "
                f"{entry.action[:60]} "
                f"({entry.latency_ms:.0f}ms, ${entry.step_cost:.4f})"
            )
            if entry.error:
                lines.append(f"    └─ Error: {entry.error[:80]}")
        lines.append(
            f"═══ Total: {self.step_count} steps, "
            f"{self.total_latency_ms:.0f}ms, ${self.total_cost:.4f} ═══"
        )
        return "\n".join(lines)
