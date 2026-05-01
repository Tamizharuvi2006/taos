"""
TAOS Tests — Controller FSM Tests.

Tests the state machine transitions, guard functions,
and safety checks in the Controller.
"""

from __future__ import annotations

import pytest
import time
from unittest.mock import MagicMock

from taos.config.constants import FSMState, ErrorType
from taos.core.controller.controller import Controller, ControllerError
from taos.core.controller.transitions import TransitionEngine
from taos.core.state.state_manager import StateManager
from taos.core.state.state_schema import (
    GlobalState,
    PlanObject,
    PlanStep,
    StepResult,
    ReflectionResult,
    StateDelta,
)


# ═══════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def controller():
    """Create a fresh Controller."""
    return Controller()


@pytest.fixture
def initialized_state(controller):
    """Create an initialized state with a goal."""
    return controller.initialize("Test goal: search for Python 3.12 features")


@pytest.fixture
def sample_plan():
    """Create a simple test plan."""
    return PlanObject(
        steps=[
            PlanStep(id="step_1", action="Search for Python 3.12", tool="web_search"),
            PlanStep(id="step_2", action="Summarize results", tool=None),
        ],
        max_steps=15,
        cost_budget=1.0,
    )


@pytest.fixture
def success_step_result():
    """Create a successful step result."""
    return StepResult(
        step_id="step_1",
        result="Python 3.12 features include...",
        success=True,
        cost=0.001,
        latency=0.5,
        tool_name="web_search",
    )


@pytest.fixture
def failure_step_result():
    """Create a failed step result."""
    return StepResult(
        step_id="step_1",
        success=False,
        error="API timeout",
        error_type=ErrorType.TIMEOUT,
        cost=0.0,
        tool_name="web_search",
    )


# ═══════════════════════════════════════════════════════════
# INITIALIZATION TESTS
# ═══════════════════════════════════════════════════════════

class TestControllerInit:
    def test_initialize_creates_state(self, controller):
        state = controller.initialize("Test goal")
        assert state.goal == "Test goal"
        assert state.current_fsm_state == FSMState.INIT
        assert state.step == 0
        assert state.cost == 0.0
        assert state.confidence == 1.0

    def test_initialize_with_request_id(self, controller):
        state = controller.initialize("Test goal", request_id="test-123")
        assert state.request_id == "test-123"

    def test_initialize_generates_request_id(self, controller):
        state = controller.initialize("Test goal")
        assert state.request_id is not None
        assert len(state.request_id) > 0


# ═══════════════════════════════════════════════════════════
# TRANSITION TESTS
# ═══════════════════════════════════════════════════════════

class TestControllerTransitions:
    def test_init_to_planning(self, controller, initialized_state):
        state = controller.transition(initialized_state, FSMState.PLANNING)
        assert state.current_fsm_state == FSMState.PLANNING

    def test_invalid_transition_raises(self, controller, initialized_state):
        with pytest.raises(ControllerError, match="Invalid transition"):
            controller.transition(initialized_state, FSMState.EXECUTING)

    def test_set_plan(self, controller, initialized_state, sample_plan):
        state = controller.transition(initialized_state, FSMState.PLANNING)
        state = controller.set_plan(state, sample_plan)
        assert state.current_fsm_state == FSMState.PLAN_READY
        assert state.plan is not None
        assert len(state.plan.steps) == 2

    def test_start_execution(self, controller, initialized_state, sample_plan):
        state = controller.transition(initialized_state, FSMState.PLANNING)
        state = controller.set_plan(state, sample_plan)
        state = controller.start_execution(state)
        assert state.current_fsm_state == FSMState.EXECUTING

    def test_state_version_increments(self, controller, initialized_state):
        initial_version = initialized_state.state_version
        state = controller.transition(initialized_state, FSMState.PLANNING)
        assert state.state_version > initial_version


# ═══════════════════════════════════════════════════════════
# STEP RESULT TESTS
# ═══════════════════════════════════════════════════════════

class TestControllerStepResults:
    def _get_executing_state(self, controller, sample_plan):
        state = controller.initialize("Test goal")
        state = controller.transition(state, FSMState.PLANNING)
        state = controller.set_plan(state, sample_plan)
        state = controller.start_execution(state)
        return state

    def test_record_step_result_transitions_to_reflecting(
        self, controller, sample_plan, success_step_result
    ):
        state = self._get_executing_state(controller, sample_plan)
        state = controller.record_step_result(state, success_step_result)
        assert state.current_fsm_state == FSMState.REFLECTING
        assert state.step == 1
        assert len(state.step_results) == 1

    def test_step_cost_tracked(self, controller, sample_plan, success_step_result):
        state = self._get_executing_state(controller, sample_plan)
        state = controller.record_step_result(state, success_step_result)
        assert state.cost == success_step_result.cost


# ═══════════════════════════════════════════════════════════
# REFLECTION HANDLING TESTS
# ═══════════════════════════════════════════════════════════

class TestControllerReflection:
    def _get_reflecting_state(self, controller, sample_plan, step_result):
        state = controller.initialize("Test goal")
        state = controller.transition(state, FSMState.PLANNING)
        state = controller.set_plan(state, sample_plan)
        state = controller.start_execution(state)
        state = controller.record_step_result(state, step_result)
        return state

    def test_high_confidence_continues_executing(
        self, controller, sample_plan, success_step_result
    ):
        state = self._get_reflecting_state(controller, sample_plan, success_step_result)
        reflection = ReflectionResult(success=True, confidence=0.85)
        state = controller.handle_reflection(state, reflection)
        assert state.current_fsm_state == FSMState.EXECUTING

    def test_low_confidence_terminates(
        self, controller, sample_plan, success_step_result
    ):
        state = self._get_reflecting_state(controller, sample_plan, success_step_result)
        reflection = ReflectionResult(success=False, confidence=0.1)
        state = controller.handle_reflection(state, reflection)
        assert state.current_fsm_state == FSMState.TERMINATING


# ═══════════════════════════════════════════════════════════
# FAILURE TESTS
# ═══════════════════════════════════════════════════════════

class TestControllerFailure:
    def test_fail_transitions_to_failed(self, controller, initialized_state):
        state = controller.fail(initialized_state, "Something went wrong")
        assert state.current_fsm_state == FSMState.FAILED
        assert state.status == "failed"
        assert state.error == "Something went wrong"

    def test_fail_works_from_any_state(self, controller, initialized_state):
        state = controller.transition(initialized_state, FSMState.PLANNING)
        state = controller.fail(state, "Planning failed")
        assert state.current_fsm_state == FSMState.FAILED


# ═══════════════════════════════════════════════════════════
# SAFETY CHECK TESTS
# ═══════════════════════════════════════════════════════════

class TestControllerSafetyChecks:
    def test_cost_budget_ok(self, controller, initialized_state):
        assert controller.check_cost_budget(initialized_state) is True

    def test_step_limit_ok(self, controller, initialized_state):
        assert controller.check_step_limit(initialized_state) is True

    def test_no_loop_detected_initially(self, controller):
        assert controller.check_loop_detected() is False

    def test_time_limit_ok(self, controller, initialized_state):
        assert controller.check_time_limit(initialized_state) is True


# ═══════════════════════════════════════════════════════════
# TRANSITION ENGINE TESTS
# ═══════════════════════════════════════════════════════════

class TestTransitionEngine:
    def test_valid_transitions(self):
        engine = TransitionEngine()
        state = GlobalState(goal="test", current_fsm_state=FSMState.INIT)
        assert engine.can_transition(state, FSMState.PLANNING) is True

    def test_invalid_transitions(self):
        engine = TransitionEngine()
        state = GlobalState(current_fsm_state=FSMState.INIT)
        assert engine.can_transition(state, FSMState.EXECUTING) is False

    def test_terminal_states_have_no_transitions(self):
        engine = TransitionEngine()
        state = GlobalState(current_fsm_state=FSMState.TERMINATED)
        assert engine.can_transition(state, FSMState.INIT) is False
