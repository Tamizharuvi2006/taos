"""
TAOS Step Runner — Runs individual plan steps.

Handles:
- Tool-based steps (delegates to ToolExecutor)
- Reasoning steps (LLM synthesis without tool calls)
- Retry logic for transient failures
- Step-level timeout enforcement
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional, TYPE_CHECKING

import httpx

from taos.config.constants import ErrorType, RETRYABLE_ERRORS
from taos.config.settings import get_settings
from taos.core.execution.dag_runner import DagRunner
from taos.core.state.state_schema import PlanStep, StepResult, StepType
from taos.core.tools.tool_executor import ToolExecutor

if TYPE_CHECKING:
    from taos.config.model_config import ModelOrchestration


class StepRunner:
    """
    Production step runner.
    
    Routes steps to either tool execution or LLM reasoning,
    handles retries for transient failures, and enforces timeouts.
    """

    def __init__(
        self,
        tool_executor: ToolExecutor,
        model_orchestration: Optional["ModelOrchestration"] = None,
        dag_runner: Optional[DagRunner] = None,
    ):
        self._tool_executor = tool_executor
        self._orchestration = model_orchestration
        self._settings = get_settings()
        self._dag_runner = dag_runner or DagRunner(tool_executor=tool_executor)

    async def run(
        self,
        step: PlanStep,
        context: Optional[Dict[str, Any]] = None,
        task_id: str = "",
    ) -> StepResult:
        """
        Execute a single step with retry support.
        
        If step has a tool → delegate to ToolExecutor
        If step has no tool → LLM reasoning step
        """
        # DAG nodes have internal retries; avoid double retries at step level.
        max_retries = 0 if step.step_type == StepType.DAG_EXEC else step.retry_policy.max_retries
        last_result: Optional[StepResult] = None

        for attempt in range(max_retries + 1):
            try:
                if step.step_type == StepType.DAG_EXEC:
                    result = await self._run_dag_step(step, context, task_id)
                elif step.tool:
                    result = await self._run_tool_step(step, task_id)
                else:
                    result = await self._run_reasoning_step(step, context)

                result.retries_used = attempt

                # ─── If success or non-retryable error, return ───
                if result.success:
                    return result

                if result.error_type and result.error_type not in {e.value for e in RETRYABLE_ERRORS}:
                    return result

                last_result = result

            except Exception as e:
                last_result = StepResult(
                    step_id=step.id,
                    success=False,
                    error=str(e),
                    error_type=ErrorType.UNKNOWN,
                    retries_used=attempt,
                    tool_name=step.tool,
                )

            # ─── Exponential backoff before retry ───
            if attempt < max_retries:
                backoff = min(2 ** attempt, 10)  # Cap at 10s
                await asyncio.sleep(backoff)

        return last_result or StepResult(
            step_id=step.id,
            success=False,
            error="All retries exhausted",
            error_type=ErrorType.TOOL_FAILURE,
            retries_used=max_retries,
            tool_name=step.tool,
        )

    async def _run_tool_step(self, step: PlanStep, task_id: str) -> StepResult:
        """Execute a tool-based step."""
        return await self._tool_executor.execute(
            tool_name=step.tool,
            tool_input=step.tool_input,
            task_id=task_id,
            step_id=step.id,
        )

    async def _run_dag_step(
        self,
        step: PlanStep,
        context: Optional[Dict[str, Any]],
        task_id: str,
    ) -> StepResult:
        """Execute a DAG step through the dedicated DAG runner."""
        start_time = time.time()
        dag_name = (step.dag_name or "research_v2").strip() or "research_v2"
        dag_input: Dict[str, Any] = dict(step.dag_input_template or {})

        if context:
            dag_input.setdefault("goal", context.get("goal"))
            dag_input.setdefault("query", context.get("goal"))
        if not dag_input.get("query"):
            dag_input["query"] = step.action
        dag_input.setdefault("search_type", "search")
        dag_input.setdefault("latest_query", f"{dag_input['query']} latest updates")
        dag_input.setdefault("context_query", f"{dag_input['query']} background context")
        dag_input.setdefault("official_query", f"{dag_input['query']} official statements")

        run_result = await self._dag_runner.run(
            dag_name=dag_name,
            input_data=dag_input,
            task_id=task_id,
            parent_step_id=step.id,
        )

        result_payload = {
            "dag_name": run_result.dag_name,
            "status": run_result.status,
            "execution_mode": run_result.execution_mode,
            "frontier_count": run_result.frontier_count,
            "final_output": run_result.final_output,
            "batches": run_result.batches,
            "node_results": {
                node_id: node_result.model_dump()
                for node_id, node_result in run_result.node_results.items()
            },
        }

        if run_result.status == "success":
            return StepResult(
                step_id=step.id,
                result=result_payload,
                success=True,
                latency=time.time() - start_time,
                cost=run_result.total_cost,
                tool_name=f"dag:{dag_name}",
            )

        return StepResult(
            step_id=step.id,
            result=result_payload,
            success=False,
            error=run_result.error or f"DAG '{dag_name}' failed",
            error_type=ErrorType.TOOL_FAILURE,
            latency=time.time() - start_time,
            cost=run_result.total_cost,
            tool_name=f"dag:{dag_name}",
        )

    async def _run_reasoning_step(
        self,
        step: PlanStep,
        context: Optional[Dict[str, Any]] = None,
    ) -> StepResult:
        """
        Execute a reasoning step (no tool, LLM synthesis).
        Uses the executor model for fast, cheap reasoning.
        """
        start_time = time.time()

        try:
            # Build reasoning prompt
            system_prompt = (
                "You are a reasoning engine. Analyze the given information "
                "and produce a clear, structured response. Be concise and factual."
            )

            user_prompt = f"Task: {step.action}\n\n"
            if step.description:
                user_prompt += f"Details: {step.description}\n\n"
            if context:
                # Add previous results for context
                prev_results = context.get("previous_results", [])
                if prev_results:
                    user_prompt += "Previous step results:\n"
                    for pr in prev_results:
                        user_prompt += f"- Step {pr['step_id']}: {pr.get('result_summary', 'N/A')}\n"
                    user_prompt += "\n"
                user_prompt += f"Goal: {context.get('goal', 'N/A')}\n"

            # Call LLM via OpenRouter
            headers = {
                "Authorization": f"Bearer {self._settings.openrouter_api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": self._settings.executor_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 2048,
            }

            async with httpx.AsyncClient(timeout=self._settings.max_step_time) as client:
                response = await client.post(
                    f"{self._settings.openrouter_base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()

            data = response.json()
            content = data["choices"][0]["message"]["content"]

            # Cost estimation
            usage = data.get("usage", {})
            cost = (usage.get("prompt_tokens", 0) * 0.000005) + (usage.get("completion_tokens", 0) * 0.000015)

            return StepResult(
                step_id=step.id,
                result=content,
                success=True,
                latency=time.time() - start_time,
                cost=cost,
                tool_name=None,
            )

        except Exception as e:
            return StepResult(
                step_id=step.id,
                success=False,
                error=f"Reasoning step failed: {str(e)}",
                error_type=ErrorType.PLANNER_ERROR,
                latency=time.time() - start_time,
                tool_name=None,
            )
