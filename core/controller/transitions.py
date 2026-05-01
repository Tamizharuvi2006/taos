"""
TAOS FSM Transitions — Defines and enforces valid state transitions.

The transition table is the single source of truth for what moves are allowed.
Each transition can have optional guards (conditions) and actions (side effects).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from taos.config.constants import FSMState
from taos.core.state.state_schema import GlobalState


@dataclass(frozen=True)
class Transition:
    """A single FSM transition with optional guard and action."""

    from_state: str
    to_state: str
    guard: Optional[Callable[[GlobalState], bool]] = None
    description: str = ""

    def is_allowed(self, state: GlobalState) -> bool:
        """Check if this transition is allowed given current state."""
        if self.guard is not None:
            return self.guard(state)
        return True


# ═══════════════════════════════════════════════════════════
# GUARD FUNCTIONS
# ═══════════════════════════════════════════════════════════

def _has_goal(state: GlobalState) -> bool:
    return bool(state.goal)


def _has_plan(state: GlobalState) -> bool:
    return state.plan is not None and len(state.plan.steps) > 0


def _has_remaining_steps(state: GlobalState) -> bool:
    if state.plan is None:
        return False
    return state.step < len(state.plan.steps)


def _confidence_above_terminate(state: GlobalState) -> bool:
    return state.confidence >= 0.3


def _confidence_below_terminate(state: GlobalState) -> bool:
    return state.confidence < 0.3


def _can_replan(state: GlobalState) -> bool:
    return state.replan_count < 2  # max 2 replans


# ═══════════════════════════════════════════════════════════
# TRANSITION TABLE
# ═══════════════════════════════════════════════════════════

TRANSITIONS: List[Transition] = [
    # INIT → PLANNING (always, if goal exists)
    Transition(FSMState.INIT, FSMState.PLANNING, guard=_has_goal,
               description="Start planning when goal is set"),

    # PLANNING → PLAN_READY (plan generated successfully)
    Transition(FSMState.PLANNING, FSMState.PLAN_READY,
               description="Plan generated and validated"),

    # PLANNING → FAILED (plan generation failed)
    Transition(FSMState.PLANNING, FSMState.FAILED,
               description="Plan generation failed"),

    # PLAN_READY → EXECUTING (begin execution)
    Transition(FSMState.PLAN_READY, FSMState.EXECUTING, guard=_has_plan,
               description="Begin executing plan steps"),

    # EXECUTING → REFLECTING (step completed, evaluate)
    Transition(FSMState.EXECUTING, FSMState.REFLECTING,
               description="Step completed, reflect on result"),

    # EXECUTING → TERMINATING (all steps done or critical failure)
    Transition(FSMState.EXECUTING, FSMState.TERMINATING,
               description="Execution terminated early"),

    # REFLECTING → EXECUTING (continue with next step)
    Transition(FSMState.REFLECTING, FSMState.EXECUTING, guard=_has_remaining_steps,
               description="Continue to next step"),

    # REFLECTING → REPLANNING (low confidence, replan)
    Transition(FSMState.REFLECTING, FSMState.REPLANNING, guard=_can_replan,
               description="Low confidence triggered replanning"),

    # REFLECTING → TERMINATING (all steps done or terminate condition)
    Transition(FSMState.REFLECTING, FSMState.TERMINATING,
               description="Reflection decided to terminate"),

    # REPLANNING → PLAN_READY (new plan generated)
    Transition(FSMState.REPLANNING, FSMState.PLAN_READY,
               description="New plan ready after replanning"),

    # REPLANNING → TERMINATING (replanning failed)
    Transition(FSMState.REPLANNING, FSMState.TERMINATING,
               description="Replanning failed, terminating"),

    # REPLANNING → FAILED (replanning gave up)
    Transition(FSMState.REPLANNING, FSMState.FAILED,
               description="Replanning exhausted"),

    # TERMINATING → TERMINATED (clean shutdown)
    Transition(FSMState.TERMINATING, FSMState.TERMINATED,
               description="Clean termination complete"),

    # Any → FAILED (catch-all for unrecoverable errors)
    Transition(FSMState.INIT, FSMState.FAILED, description="Init failed"),
    Transition(FSMState.PLAN_READY, FSMState.FAILED, description="Unexpected failure"),
    Transition(FSMState.EXECUTING, FSMState.FAILED, description="Unrecoverable execution failure"),
    Transition(FSMState.REFLECTING, FSMState.FAILED, description="Reflection critical failure"),
]


class TransitionEngine:
    """Manages and validates FSM transitions."""

    def __init__(self) -> None:
        self._transition_map: Dict[str, List[Transition]] = {}
        for t in TRANSITIONS:
            self._transition_map.setdefault(t.from_state, []).append(t)

    def get_valid_transitions(self, state: GlobalState) -> List[Transition]:
        """Get all valid transitions from the current FSM state."""
        candidates = self._transition_map.get(state.current_fsm_state, [])
        return [t for t in candidates if t.is_allowed(state)]

    def can_transition(self, state: GlobalState, to_state: str) -> bool:
        """Check if a specific transition is valid."""
        candidates = self._transition_map.get(state.current_fsm_state, [])
        return any(
            t.to_state == to_state and t.is_allowed(state) 
            for t in candidates
        )

    def get_transition(self, from_state: str, to_state: str) -> Optional[Transition]:
        """Find a specific transition definition."""
        candidates = self._transition_map.get(from_state, [])
        for t in candidates:
            if t.to_state == to_state:
                return t
        return None
