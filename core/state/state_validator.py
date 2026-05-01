"""
TAOS State Validator — Ensures all state transitions are legal.

Validates:
- FSM transition legality
- Required field presence
- Value range constraints
- State consistency invariants
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional

from taos.config.constants import FSMState
from taos.core.state.state_schema import GlobalState, StateDelta


# ═══════════════════════════════════════════════════════════
# VALID FSM TRANSITIONS
# ═══════════════════════════════════════════════════════════

VALID_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    FSMState.INIT: frozenset({FSMState.PLANNING, FSMState.FAILED}),
    FSMState.PLANNING: frozenset({FSMState.PLAN_READY, FSMState.FAILED}),
    FSMState.PLAN_READY: frozenset({FSMState.EXECUTING, FSMState.FAILED}),
    FSMState.EXECUTING: frozenset({FSMState.REFLECTING, FSMState.FAILED, FSMState.TERMINATING}),
    FSMState.REFLECTING: frozenset({FSMState.EXECUTING, FSMState.REPLANNING, FSMState.TERMINATING, FSMState.FAILED}),
    FSMState.REPLANNING: frozenset({FSMState.PLAN_READY, FSMState.TERMINATING, FSMState.FAILED}),
    FSMState.TERMINATING: frozenset({FSMState.TERMINATED}),
    FSMState.TERMINATED: frozenset(),  # Terminal state
    FSMState.FAILED: frozenset(),       # Terminal state
}


class StateValidationError(Exception):
    """Raised when state validation fails."""

    def __init__(self, message: str, field: Optional[str] = None):
        self.field = field
        super().__init__(message)


class StateValidator:
    """Production state validator with comprehensive checks."""

    @staticmethod
    def validate_fsm_transition(from_state: str, to_state: str) -> bool:
        """Check if an FSM transition is legal."""
        valid_targets = VALID_TRANSITIONS.get(from_state, frozenset())
        return to_state in valid_targets

    @staticmethod
    def validate_delta(state: GlobalState, delta: StateDelta) -> list[str]:
        """
        Validate a delta against current state. Returns list of violations.
        Empty list = valid.
        """
        violations: list[str] = []

        # ─── FSM transition check ───
        if delta.fsm_state_update:
            if not StateValidator.validate_fsm_transition(
                state.current_fsm_state, delta.fsm_state_update
            ):
                violations.append(
                    f"Invalid FSM transition: {state.current_fsm_state} → {delta.fsm_state_update}"
                )

        # ─── Confidence bounds ───
        if delta.confidence_update is not None:
            if not (0.0 <= delta.confidence_update <= 1.0):
                violations.append(
                    f"Confidence out of range: {delta.confidence_update} (must be 0.0-1.0)"
                )

        # ─── Cost must be positive ───
        if delta.cost_increment < 0:
            violations.append(f"Negative cost increment: {delta.cost_increment}")

        # ─── Step increment must be non-negative ───
        if delta.step_increment < 0:
            violations.append(f"Negative step increment: {delta.step_increment}")

        # ─── Cannot update terminated state ───
        if state.current_fsm_state in (FSMState.TERMINATED, FSMState.FAILED):
            if delta.fsm_state_update and delta.fsm_state_update not in (
                FSMState.TERMINATED, FSMState.FAILED
            ):
                violations.append("Cannot transition from terminal state")

        return violations

    @staticmethod
    def validate_state_consistency(state: GlobalState) -> list[str]:
        """Check state-level consistency invariants."""
        violations: list[str] = []

        # Goal must be set
        if not state.goal:
            violations.append("State has no goal")

        # Step count must match results
        if state.step > 0 and len(state.step_results) > state.step:
            violations.append(
                f"More step results ({len(state.step_results)}) than steps ({state.step})"
            )

        # Cost must be non-negative
        if state.cost < 0:
            violations.append(f"Negative total cost: {state.cost}")

        # Confidence must be in range
        if not (0.0 <= state.confidence <= 1.0):
            violations.append(f"Confidence out of range: {state.confidence}")

        # Version must be non-negative
        if state.state_version < 0:
            violations.append(f"Negative state version: {state.state_version}")

        return violations
