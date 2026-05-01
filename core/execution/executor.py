"""
TAOS Executor — Orchestrates sequential execution of plan steps.

Production features:
- Sequential step execution with state tracking
- Cost budget enforcement mid-execution
- Step limit enforcement
- Time limit enforcement
- Per-step timeout handling
- Aggregated results collection
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from taos.config.settings import get_settings
from taos.core.state.state_schema import GlobalState, PlanObject, PlanStep, StepResult
from taos.core.execution.step_runner import StepRunner
from taos.core.execution.result_handler import ResultHandler

if TYPE_CHECKING:
    from taos.infra.logging.logger import TAOSLogger


class ExecutionError(Exception):
    """Raised on unrecoverable execution failure."""
    pass


class Executor:
    """
    Production plan executor.
    
    Runs plan steps sequentially, enforcing budgets and limits.
    Returns results one step at a time for the Controller to process.
    """

    def __init__(
        self,
        step_runner: StepRunner,
        result_handler: Optional[ResultHandler] = None,
        logger: Optional["TAOSLogger"] = None,
    ):
        self._step_runner = step_runner
        self._result_handler = result_handler or ResultHandler()
        self._logger = logger
        self._settings = get_settings()

    async def execute_step(
        self,
        step: PlanStep,
        state: GlobalState,
        step_index: int,
    ) -> StepResult:
        """
        Execute a single plan step.
        
        Called by the orchestration engine in the execution loop.
        Returns a StepResult for the Controller to process.
        """
        self._log(
            "executor.step_start",
            step_id=step.id,
            step_index=step_index,
            tool=step.tool,
            action=step.action,
        )

        # ─── Pre-execution checks ───
        elapsed = time.time() - state.created_at
        if elapsed >= self._settings.max_task_time:
            return StepResult(
                step_id=step.id,
                success=False,
                error=f"Task time limit exceeded ({elapsed:.1f}s >= {self._settings.max_task_time}s)",
                error_type="TIMEOUT",
                tool_name=step.tool,
            )

        if state.cost >= self._settings.cost_budget_per_task:
            return StepResult(
                step_id=step.id,
                success=False,
                error=f"Cost budget exceeded (${state.cost:.4f} >= ${self._settings.cost_budget_per_task:.4f})",
                error_type="COST_EXCEEDED",
                tool_name=step.tool,
            )

        # ─── Execute the step ───
        result = await self._step_runner.run(
            step=step,
            context=self._build_step_context(state, step_index),
            task_id=state.request_id,
        )

        # ─── Post-process result ───
        processed = self._result_handler.process(result, step, state)

        self._log(
            "executor.step_complete",
            step_id=step.id,
            success=processed.success,
            latency=processed.latency,
            cost=processed.cost,
        )

        return processed

    async def execute_all(
        self,
        plan: PlanObject,
        state: GlobalState,
    ) -> List[StepResult]:
        """
        Execute all steps in a plan sequentially.
        
        Note: In production, the orchestration engine calls execute_step()
        one at a time with reflection between steps. This method is for
        testing and simple execution without reflection.
        """
        results: List[StepResult] = []

        for i, step in enumerate(plan.steps):
            result = await self.execute_step(step, state, i)
            results.append(result)

            if not result.success:
                self._log("executor.step_failed", step_id=step.id, error=result.error)
                break

        return results

    def _build_step_context(self, state: GlobalState, step_index: int) -> Dict[str, Any]:
        """Build context dict for step execution."""
        context: Dict[str, Any] = {
            "goal": state.goal,
            "step_index": step_index,
            "total_steps": len(state.plan.steps) if state.plan else 0,
            "previous_results": [],
        }

        # Include previous step results for context
        for sr in state.step_results[-3:]:  # Last 3 results
            context["previous_results"].append({
                "step_id": sr.step_id,
                "tool": sr.tool_name,
                "success": sr.success,
                "result_summary": str(sr.result)[:500] if sr.result else None,
            })

        return context

    def _log(self, event: str, **kwargs) -> None:
        if self._logger:
            self._logger.info(event, **kwargs)
