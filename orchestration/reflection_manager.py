from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from taos.config.constants import FSMState
from taos.core.state.state_schema import GlobalState, PlanStep, StepResult


@dataclass(frozen=True)
class ReflectionDecision:
    state: str
    should_replan: bool
    should_terminate: bool


class ReflectionManager:
    """Owns reflection result application and replan/terminate mapping."""

    def decision_for_state(self, state: GlobalState) -> ReflectionDecision:
        current = getattr(state.current_fsm_state, "value", str(state.current_fsm_state))
        return ReflectionDecision(
            state=current,
            should_replan=state.current_fsm_state == FSMState.REPLANNING,
            should_terminate=state.current_fsm_state == FSMState.TERMINATING,
        )

    async def reflect_and_apply(
        self,
        *,
        engine: Any,
        state: GlobalState,
        step: PlanStep,
        step_result: StepResult,
        apply_research_guard: bool = True,
    ) -> tuple[GlobalState, Any, ReflectionDecision]:
        reflection = await engine._reflector.reflect(step_result=step_result, step=step, state=state)
        if apply_research_guard:
            reflection = engine._apply_research_reflection_guard(
                reflection=reflection,
                step_result=step_result,
                step=step,
                state=state,
            )
        engine._memory.store_reflection(step.id, reflection)
        engine._confidence_scorer.record(reflection.confidence)
        state = engine._controller.handle_reflection(state, reflection)
        if state.current_fsm_state == FSMState.REPLANNING:
            state = await engine._handle_replanning(state)
        return state, reflection, self.decision_for_state(state)
