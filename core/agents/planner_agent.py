"""PlannerAgent wrapper around planning + analysis execution behavior."""

from __future__ import annotations

from typing import List, Optional, Tuple

from taos.core.agents.base_agent import BaseAgent
from taos.core.execution.executor import Executor
from taos.core.planner.planner import Planner
from taos.core.state.state_schema import GlobalState, PlanObject, PlanStep, StepResult


class PlannerAgent(BaseAgent):
    """
    Planner-specialized agent.

    Responsibilities:
    - Generate full plans (existing behavior).
    - Execute analysis/comparison style steps when routed by AgentRouter.
    """

    def __init__(self, planner: Planner, executor: Executor) -> None:
        self._planner = planner
        self._executor = executor

    @property
    def name(self) -> str:
        return "planner_agent"

    async def generate_plan(
        self,
        goal: str,
        context: Optional[List[str]] = None,
        intent: Optional[str] = None,
        available_tools_override: Optional[list[str]] = None,
    ) -> Tuple[PlanObject, float]:
        return await self._planner.generate_plan(
            goal=goal,
            context=context,
            intent=intent,
            available_tools_override=available_tools_override,
        )

    async def execute(
        self,
        step: PlanStep,
        state: GlobalState,
        step_index: int,
    ) -> StepResult:
        # Analysis/comparison steps still flow through the hardened executor path.
        return await self._executor.execute_step(
            step=step,
            state=state,
            step_index=step_index,
        )
