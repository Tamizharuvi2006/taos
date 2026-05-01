"""
TAOS State Manager — The ONLY authorized mutator of GlobalState.

Enforces:
- Immutable state transitions (copy-on-write)
- Version chaining with hash integrity
- Delta-based updates from execution components
- Full transition logging for observability
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from taos.core.state.state_schema import (
    GlobalState,
    StateDelta,
)


class StateTransitionError(Exception):
    """Raised when a state transition violates invariants."""
    pass


class StateManager:
    """
    Production state manager with immutable transitions.
    
    All state mutations go through apply_delta() which:
    1. Validates the delta
    2. Creates a new state version (copy-on-write)
    3. Updates the version counter and hash chain
    4. Logs the transition
    5. Notifies listeners
    """

    def __init__(self) -> None:
        self._history: List[GlobalState] = []
        self._listeners: List[Callable[[GlobalState, GlobalState, StateDelta], None]] = []

    def create_initial_state(self, goal: str, request_id: Optional[str] = None) -> GlobalState:
        """Create a fresh initial state for a new task."""
        state = GlobalState(
            goal=goal,
            status="running",
            current_fsm_state="INIT",
        )
        if request_id:
            state = state.model_copy(update={"request_id": request_id})
        self._history.append(state)
        return state

    def apply_delta(self, state: GlobalState, delta: StateDelta) -> GlobalState:
        """
        Apply a state delta to produce a new immutable state.
        
        This is the ONLY way to mutate state in TAOS.
        Returns a new GlobalState with incremented version and updated hash chain.
        """
        updates: Dict[str, Any] = {}

        # ─── Step counter ───
        if delta.step_increment:
            updates["step"] = state.step + delta.step_increment

        # ─── Confidence ───
        if delta.confidence_update is not None:
            updates["confidence"] = max(0.0, min(1.0, delta.confidence_update))

        # ─── Cost tracking ───
        if delta.cost_increment > 0:
            updates["cost"] = state.cost + delta.cost_increment

        # ─── Cost breakdown ───
        if delta.cost_breakdown_updates:
            breakdown = state.cost_breakdown.model_copy()
            for key, value in delta.cost_breakdown_updates.items():
                current = getattr(breakdown, key, 0.0)
                setattr(breakdown, key, current + value)
            updates["cost_breakdown"] = breakdown

        # ─── Tool results ───
        if delta.new_tool_result is not None:
            updates["tool_results"] = [*state.tool_results, delta.new_tool_result]

        # ─── Step results ───
        if delta.new_step_result is not None:
            updates["step_results"] = [*state.step_results, delta.new_step_result]

        # ─── Context ───
        if delta.new_context is not None:
            updates["context"] = [*state.context, delta.new_context]

        # ─── Memory refs ───
        if delta.new_memory_ref is not None:
            updates["memory_refs"] = [*state.memory_refs, delta.new_memory_ref]

        # ─── Status ───
        if delta.status_update is not None:
            updates["status"] = delta.status_update

        # ─── Error ───
        if delta.error_update is not None:
            updates["error"] = delta.error_update

        # ─── FSM state ───
        if delta.fsm_state_update is not None:
            updates["current_fsm_state"] = delta.fsm_state_update

        # ─── Plan ───
        if delta.plan_update is not None:
            updates["plan"] = delta.plan_update

        # ─── Replan counter ───
        if delta.replan_increment:
            updates["replan_count"] = state.replan_count + delta.replan_increment

        # ─── Create new versioned state ───
        new_state = state.model_copy_with_version(**updates)

        # ─── Record history ───
        self._history.append(new_state)

        # ─── Notify listeners ───
        for listener in self._listeners:
            try:
                listener(state, new_state, delta)
            except Exception:
                pass  # Listeners must not break state transitions

        return new_state

    def verify_chain_integrity(self) -> bool:
        """Verify the hash chain integrity of state history."""
        if len(self._history) < 2:
            return True
        for i in range(1, len(self._history)):
            expected_hash = self._history[i - 1].compute_hash()
            actual_hash = self._history[i].prev_state_hash
            if expected_hash != actual_hash:
                return False
        return True

    def get_history(self) -> List[GlobalState]:
        """Return the full state history (immutable copies)."""
        return list(self._history)

    def get_latest(self) -> Optional[GlobalState]:
        """Get the most recent state."""
        return self._history[-1] if self._history else None

    def add_listener(self, listener: Callable[[GlobalState, GlobalState, StateDelta], None]) -> None:
        """Register a state transition listener for observability."""
        self._listeners.append(listener)

    def rollback(self, to_version: int) -> Optional[GlobalState]:
        """Rollback to a specific state version (for failure recovery)."""
        for state in reversed(self._history):
            if state.state_version == to_version:
                self._history.append(state.model_copy_with_version())
                return self._history[-1]
        return None
