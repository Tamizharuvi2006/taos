"""
TAOS Progress Tracker — Speed perception for users.

UPGRADE #5: SPEED PERCEPTION (UX LOGIC)

Provides progress states and partial responses so the user
always knows what's happening, even during long operations.

States:
- classifying: Understanding query
- planning: Building execution plan
- executing: Running tools (step X of Y)
- reflecting: Evaluating results
- formatting: Preparing response
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Callable


class ProgressPhase(str, Enum):
    """Phases of agent execution."""
    RECEIVED = "received"
    CLASSIFYING = "classifying"
    FAST_PATH = "fast_path"
    PLANNING = "planning"
    EXECUTING = "executing"
    REFLECTING = "reflecting"
    FORMATTING = "formatting"
    EVALUATING = "evaluating"
    COMPLETE = "complete"
    FAILED = "failed"


# User-friendly labels
_PHASE_LABELS = {
    ProgressPhase.RECEIVED: "🔵 Query received",
    ProgressPhase.CLASSIFYING: "🔍 Understanding your query...",
    ProgressPhase.FAST_PATH: "⚡ Found quick answer",
    ProgressPhase.PLANNING: "📋 Building execution plan...",
    ProgressPhase.EXECUTING: "⚙️ Running step {step} of {total}...",
    ProgressPhase.REFLECTING: "🤔 Evaluating results...",
    ProgressPhase.FORMATTING: "✍️ Formatting response...",
    ProgressPhase.EVALUATING: "✅ Quality check...",
    ProgressPhase.COMPLETE: "🎯 Done!",
    ProgressPhase.FAILED: "❌ Something went wrong",
}

# Estimated progress percentages per phase
_PHASE_PROGRESS = {
    ProgressPhase.RECEIVED: 0,
    ProgressPhase.CLASSIFYING: 5,
    ProgressPhase.FAST_PATH: 90,
    ProgressPhase.PLANNING: 15,
    ProgressPhase.EXECUTING: 50,  # Base; actual is step/total * 60
    ProgressPhase.REFLECTING: 75,
    ProgressPhase.FORMATTING: 85,
    ProgressPhase.EVALUATING: 92,
    ProgressPhase.COMPLETE: 100,
    ProgressPhase.FAILED: 100,
}


@dataclass
class ProgressUpdate:
    """A single progress update."""
    phase: ProgressPhase
    label: str
    progress_pct: int  # 0-100
    timestamp: float = 0.0
    detail: str = ""
    partial_result: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "phase": self.phase.value,
            "label": self.label,
            "progress": self.progress_pct,
            "timestamp": self.timestamp,
        }
        if self.detail:
            d["detail"] = self.detail
        if self.partial_result:
            d["partial_result"] = self.partial_result
        return d


class ProgressTracker:
    """
    Tracks execution progress for UX speed perception.

    Can be used with:
    - SSE (Server-Sent Events) for real-time streaming
    - Polling endpoint for periodic checks
    - WebSocket for bidirectional updates
    """

    def __init__(self, request_id: str = "") -> None:
        self._request_id = request_id
        self._updates: List[ProgressUpdate] = []
        self._current_phase = ProgressPhase.RECEIVED
        self._start_time = time.time()
        self._callbacks: List[Callable] = []

    def update(
        self,
        phase: ProgressPhase,
        detail: str = "",
        step: int = 0,
        total_steps: int = 0,
        partial_result: Optional[str] = None,
    ) -> ProgressUpdate:
        """Record a progress update."""
        # Calculate progress
        progress = _PHASE_PROGRESS.get(phase, 0)
        if phase == ProgressPhase.EXECUTING and total_steps > 0:
            # Scale executing phase from 20-75% based on step progress
            step_progress = (step / total_steps) * 55
            progress = 20 + int(step_progress)

        # Build label
        label = _PHASE_LABELS.get(phase, str(phase.value))
        if phase == ProgressPhase.EXECUTING:
            label = label.format(step=step + 1, total=total_steps)

        update = ProgressUpdate(
            phase=phase,
            label=label,
            progress_pct=min(100, progress),
            timestamp=time.time(),
            detail=detail,
            partial_result=partial_result,
        )

        self._updates.append(update)
        self._current_phase = phase

        # Notify callbacks (for SSE/WebSocket)
        for cb in self._callbacks:
            try:
                cb(update)
            except Exception:
                pass

        return update

    def on_update(self, callback: Callable) -> None:
        """Register a callback for progress updates."""
        self._callbacks.append(callback)

    @property
    def current(self) -> ProgressUpdate:
        """Get the current progress state."""
        if self._updates:
            return self._updates[-1]
        return ProgressUpdate(
            phase=ProgressPhase.RECEIVED,
            label="Waiting...",
            progress_pct=0,
        )

    @property
    def history(self) -> List[Dict[str, Any]]:
        """Get all progress updates."""
        return [u.to_dict() for u in self._updates]

    @property
    def elapsed_ms(self) -> float:
        return (time.time() - self._start_time) * 1000

    @property
    def is_complete(self) -> bool:
        return self._current_phase in (ProgressPhase.COMPLETE, ProgressPhase.FAILED)


# ═══════════════════════════════════════════════════════════
# GLOBAL PROGRESS STORE (for polling endpoint)
# ═══════════════════════════════════════════════════════════

_active_trackers: Dict[str, ProgressTracker] = {}
_tracker_owner: Dict[str, str] = {}


def create_tracker(request_id: str) -> ProgressTracker:
    """Create and register a progress tracker."""
    tracker = ProgressTracker(request_id=request_id)
    _active_trackers[request_id] = tracker
    return tracker


def get_tracker(request_id: str) -> Optional[ProgressTracker]:
    """Get an active progress tracker."""
    return _active_trackers.get(request_id)


def remove_tracker(request_id: str) -> None:
    """Remove a completed tracker."""
    _active_trackers.pop(request_id, None)
    _tracker_owner.pop(request_id, None)


def active_tracker_count() -> int:
    return len(_active_trackers)


def register_tracker_owner(request_id: str, user_id: str) -> None:
    _tracker_owner[request_id] = user_id


def get_tracker_owner(request_id: str) -> Optional[str]:
    return _tracker_owner.get(request_id)
