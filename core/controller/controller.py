"""
TAOS Controller — The deterministic brain of the agent.

This is the ONLY component authorized to mutate GlobalState.
It drives the entire PLAN → EXECUTE → REFLECT → TERMINATE lifecycle
via a Finite State Machine.

Production features:
- FSM-driven execution with validated transitions
- Cost budget enforcement
- Loop detection and auto-termination
- Dynamic replanning on failure
- Full state history and observability
"""

from __future__ import annotations

import time
from typing import Any, List, Optional, TYPE_CHECKING

from taos.config.constants import ErrorType, FSMState
from taos.config.settings import get_settings
from taos.core.controller.transitions import TransitionEngine
from taos.core.state.state_diff import compute_diff
from taos.core.state.state_manager import StateManager
from taos.core.state.state_schema import (
    GlobalState,
    PlanObject,
    StateDelta,
    StepResult,
    ReflectionResult,
)
from taos.core.state.state_validator import StateValidator

if TYPE_CHECKING:
    from taos.infra.logging.logger import TAOSLogger


class ControllerError(Exception):
    """Raised when the controller encounters an unrecoverable error."""
    pass


class Controller:
    """
    Production FSM controller.
    
    Orchestrates the entire agent lifecycle:
    1. Receive goal → transition to PLANNING
    2. Generate plan → transition to PLAN_READY
    3. Execute steps → EXECUTING ↔ REFLECTING loop
    4. Handle failures → REPLANNING or TERMINATING
    5. Produce final result → TERMINATED
    """

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        logger: Optional["TAOSLogger"] = None,
    ):
        self._state_manager = state_manager or StateManager()
        self._transition_engine = TransitionEngine()
        self._validator = StateValidator()
        self._logger = logger
        self._settings = get_settings()
        self._state_hashes: List[str] = []  # For loop detection

    # ═══════════════════════════════════════════════════════
    # STATE TRANSITIONS
    # ═══════════════════════════════════════════════════════

    def initialize(self, goal: str, request_id: Optional[str] = None) -> GlobalState:
        """Initialize a new task with a goal."""
        state = self._state_manager.create_initial_state(goal, request_id)
        self._log("controller.initialized", goal=goal, request_id=state.request_id)
        return state

    def transition(self, state: GlobalState, to_state: str, **delta_kwargs) -> GlobalState:
        """
        Execute a validated FSM transition.
        
        Validates the transition, applies the delta, and logs the change.
        Raises ControllerError if the transition is invalid.
        """
        # ─── Validate transition ───
        if not self._transition_engine.can_transition(state, to_state):
            raise ControllerError(
                f"Invalid transition: {state.current_fsm_state} → {to_state}"
            )

        # ─── Build delta ───
        delta = StateDelta(
            fsm_state_update=to_state,
            **delta_kwargs,
        )

        # ─── Validate delta ───
        violations = self._validator.validate_delta(state, delta)
        if violations:
            raise ControllerError(f"Delta validation failed: {violations}")

        # ─── Apply ───
        new_state = self._state_manager.apply_delta(state, delta)

        # ─── Log transition ───
        diff = compute_diff(state, new_state)
        self._log(
            "controller.transition",
            from_state=state.current_fsm_state,
            to_state=to_state,
            version=new_state.state_version,
            changes=diff.changed_fields,
        )

        # ─── Track hash for loop detection ───
        self._state_hashes.append(new_state.compute_hash())

        return new_state

    # ═══════════════════════════════════════════════════════
    # PLAN MANAGEMENT
    # ═══════════════════════════════════════════════════════

    def set_plan(self, state: GlobalState, plan: PlanObject) -> GlobalState:
        """Transition to PLAN_READY with a validated plan."""
        return self.transition(
            state,
            FSMState.PLAN_READY,
            plan_update=plan,
        )

    def start_execution(self, state: GlobalState) -> GlobalState:
        """Transition from PLAN_READY to EXECUTING."""
        return self.transition(state, FSMState.EXECUTING)

    # ═══════════════════════════════════════════════════════
    # STEP EXECUTION
    # ═══════════════════════════════════════════════════════

    def record_step_result(
        self, state: GlobalState, step_result: StepResult
    ) -> GlobalState:
        """
        Record a step result and transition to REFLECTING.
        Updates cost, step counter, and results list.
        """
        return self.transition(
            state,
            FSMState.REFLECTING,
            step_increment=1,
            cost_increment=step_result.cost,
            new_step_result=step_result,
            new_tool_result={
                "step_id": step_result.step_id,
                "tool": step_result.tool_name,
                "result": step_result.result,
                "success": step_result.success,
            } if step_result.tool_name else None,
            cost_breakdown_updates={
                "execution_cost": step_result.cost,
            },
        )

    # ═══════════════════════════════════════════════════════
    # REFLECTION HANDLING
    # ═══════════════════════════════════════════════════════

    def handle_reflection(
        self, state: GlobalState, reflection: ReflectionResult
    ) -> GlobalState:
        """
        Process reflection result and decide next action:
        - confidence >= 0.6 and more steps → continue EXECUTING
        - confidence < 0.6 and can replan → REPLANNING
        - confidence < 0.3 or no more steps → TERMINATING
        - all steps completed successfully → TERMINATING (success)
        """
        new_confidence = reflection.confidence

        # Add reflection context
        context_entry = f"Reflection (step {state.step}): confidence={reflection.confidence:.2f}, {reflection.reasoning}"

        # ─── All steps completed? ───
        all_done = (
            state.plan is not None
            and state.step >= len(state.plan.steps)
        )

        # ─── Determine next state ───
        if all_done and reflection.success:
            # All steps completed successfully
            return self.transition(
                state,
                FSMState.TERMINATING,
                confidence_update=new_confidence,
                status_update="success",
                new_context=context_entry,
            )

        if new_confidence < self._settings.confidence_terminate_threshold:
            # Very low confidence — terminate
            return self.transition(
                state,
                FSMState.TERMINATING,
                confidence_update=new_confidence,
                status_update="failed",
                error_update=f"Confidence too low: {new_confidence:.2f}",
                new_context=context_entry,
            )

        if new_confidence < self._settings.confidence_retry_threshold and reflection.retry_recommended:
            # Low confidence — attempt replan if possible
            if self._transition_engine.can_transition(state, FSMState.REPLANNING):
                return self.transition(
                    state,
                    FSMState.REPLANNING,
                    confidence_update=new_confidence,
                    replan_increment=1,
                    new_context=context_entry,
                )

        if not all_done:
            # More steps to execute
            return self.transition(
                state,
                FSMState.EXECUTING,
                confidence_update=new_confidence,
                new_context=context_entry,
            )

        # Fallthrough: all done but not fully successful
        return self.transition(
            state,
            FSMState.TERMINATING,
            confidence_update=new_confidence,
            status_update="success" if reflection.success else "failed",
            new_context=context_entry,
        )

    # ═══════════════════════════════════════════════════════
    # REPLANNING
    # ═══════════════════════════════════════════════════════

    def set_replan(self, state: GlobalState, new_plan: PlanObject) -> GlobalState:
        """Apply a new plan from replanning and transition to PLAN_READY."""
        return self.transition(
            state,
            FSMState.PLAN_READY,
            plan_update=new_plan,
            new_context=f"Replanned (attempt {state.replan_count})",
        )

    def fail_replan(self, state: GlobalState, reason: str) -> GlobalState:
        """Handle replanning failure."""
        return self.transition(
            state,
            FSMState.TERMINATING,
            status_update="failed",
            error_update=f"Replanning failed: {reason}",
        )

    # ═══════════════════════════════════════════════════════
    # TERMINATION
    # ═══════════════════════════════════════════════════════

    def terminate(self, state: GlobalState, final_result: Any = None) -> GlobalState:
        """Complete termination and produce final state."""
        return self.transition(
            state,
            FSMState.TERMINATED,
        )

    def fail(self, state: GlobalState, error: str, error_type: str = ErrorType.UNKNOWN) -> GlobalState:
        """Transition to FAILED state on unrecoverable error."""
        try:
            return self.transition(
                state,
                FSMState.FAILED,
                status_update="failed",
                error_update=error,
            )
        except ControllerError:
            # If we can't even transition to FAILED, force it
            delta = StateDelta(
                fsm_state_update=FSMState.FAILED,
                status_update="failed",
                error_update=error,
            )
            return self._state_manager.apply_delta(state, delta)

    # ═══════════════════════════════════════════════════════
    # SAFETY CHECKS
    # ═══════════════════════════════════════════════════════

    def check_cost_budget(self, state: GlobalState) -> bool:
        """Check if task is within cost budget."""
        return state.cost < self._settings.cost_budget_per_task

    def check_step_limit(self, state: GlobalState) -> bool:
        """Check if task is within step limit."""
        return state.step < self._settings.max_steps

    def check_loop_detected(self) -> bool:
        """
        Detect execution loops by checking for repeated state hashes.
        Returns True if a loop is detected.
        """
        threshold = self._settings.repeated_state_threshold
        if len(self._state_hashes) < threshold:
            return False
        recent = self._state_hashes[-threshold:]
        return len(set(recent)) == 1  # All same hash = loop

    def check_time_limit(self, state: GlobalState) -> bool:
        """Check if task has exceeded time limit."""
        elapsed = time.time() - state.created_at
        return elapsed < self._settings.max_task_time

    # ═══════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════

    @property
    def state_manager(self) -> StateManager:
        return self._state_manager

    def get_history(self) -> List[GlobalState]:
        return self._state_manager.get_history()

    def _log(self, event: str, **kwargs) -> None:
        """Internal logging helper."""
        if self._logger:
            self._logger.info(event, **kwargs)
