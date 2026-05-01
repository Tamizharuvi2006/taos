"""ExecutionAgent wraps the existing Executor for agent-routed execution."""

from __future__ import annotations

from taos.core.agents.base_agent import BaseAgent
from taos.core.execution.executor import Executor
from taos.core.state.state_schema import GlobalState, PlanStep, StepResult


class ExecutionAgent(BaseAgent):
    """
    Specialized agent responsible for running executable plan steps.

    Internally delegates to the existing Executor to preserve behavior,
    tool governance, retries, result handling, and cost/time checks.
    """

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    @property
    def name(self) -> str:
        return "execution_agent"

    async def execute(
        self,
        step: PlanStep,
        state: GlobalState,
        step_index: int,
    ) -> StepResult:
        return await self._executor.execute_step(
            step=step,
            state=state,
            step_index=step_index,
        )
