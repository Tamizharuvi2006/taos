"""
TAOS Workflow — High-level workflow definitions.

Provides pre-defined workflow patterns for common agent tasks.
These are convenience wrappers around the OrchestrationEngine
for specific execution patterns.

PRD Reference: §6 (Core Loop)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from taos.config.settings import get_settings

if TYPE_CHECKING:
    from taos.orchestration.engine import OrchestrationEngine


# ═══════════════════════════════════════════════════════════
# WORKFLOW RESULT
# ═══════════════════════════════════════════════════════════

@dataclass
class WorkflowResult:
    """Result of a workflow execution."""

    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    elapsed_time: float = 0.0
    workflow_type: str = "standard"


# ═══════════════════════════════════════════════════════════
# WORKFLOW RUNNER
# ═══════════════════════════════════════════════════════════

class WorkflowRunner:
    """
    High-level workflow runner for common agent execution patterns.

    Workflows:
    1. standard — Full PLAN → EXECUTE → REFLECT cycle
    2. plan_only — Generate and return a plan without executing
    3. direct — Execute a pre-built plan without LLM planning
    4. batch — Execute multiple goals sequentially
    """

    def __init__(self, engine: "OrchestrationEngine") -> None:
        self._engine = engine
        self._settings = get_settings()

    async def run_standard(
        self,
        goal: str,
        request_id: Optional[str] = None,
    ) -> WorkflowResult:
        """
        Standard workflow: full PLAN → EXECUTE → REFLECT cycle.

        This is the default workflow used by the API endpoint.
        """
        start_time = time.time()

        try:
            result = await self._engine.run(goal=goal, request_id=request_id)

            return WorkflowResult(
                success=result.get("success", False),
                result=result,
                elapsed_time=time.time() - start_time,
                workflow_type="standard",
            )
        except Exception as e:
            return WorkflowResult(
                success=False,
                error=str(e),
                elapsed_time=time.time() - start_time,
                workflow_type="standard",
            )

    async def run_plan_only(
        self,
        goal: str,
    ) -> WorkflowResult:
        """
        Plan-only workflow: generate a plan without executing.

        Useful for previewing what the agent would do, or for
        human-in-the-loop approval before execution.
        """
        start_time = time.time()

        try:
            from taos.core.validation.goal_validator import GoalValidator
            from taos.core.planner.planner import Planner

            validator = GoalValidator()
            goal_result = validator.validate(goal)

            if not goal_result.is_valid:
                return WorkflowResult(
                    success=False,
                    error=f"Goal validation failed: {'; '.join(goal_result.errors)}",
                    elapsed_time=time.time() - start_time,
                    workflow_type="plan_only",
                )

            planner = Planner(
                available_tools=self._engine._tool_registry.list_names(),
            )
            plan, cost = await planner.generate_plan(goal=goal)

            result = {
                "goal": goal,
                "plan": {
                    "plan_id": plan.plan_id,
                    "step_count": plan.step_count,
                    "steps": [
                        {
                            "id": s.id,
                            "action": s.action,
                            "tool": s.tool,
                            "tool_input": s.tool_input,
                            "depends_on": s.depends_on,
                            "description": s.description,
                        }
                        for s in plan.steps
                    ],
                },
                "planning_cost": cost,
                "complexity": goal_result.complexity.value,
                "estimated_steps": goal_result.estimated_steps,
                "suggested_tools": goal_result.suggested_tools,
            }

            return WorkflowResult(
                success=True,
                result=result,
                elapsed_time=time.time() - start_time,
                workflow_type="plan_only",
            )
        except Exception as e:
            return WorkflowResult(
                success=False,
                error=str(e),
                elapsed_time=time.time() - start_time,
                workflow_type="plan_only",
            )

    async def run_batch(
        self,
        goals: List[str],
        stop_on_failure: bool = False,
    ) -> List[WorkflowResult]:
        """
        Batch workflow: execute multiple goals sequentially.

        Args:
            goals: List of goal strings to execute.
            stop_on_failure: If True, stop on the first failure.

        Returns:
            List of WorkflowResult, one per goal.
        """
        results: List[WorkflowResult] = []

        for i, goal in enumerate(goals):
            result = await self.run_standard(
                goal=goal,
                request_id=f"batch_{i}",
            )
            results.append(result)

            if stop_on_failure and not result.success:
                break

        return results

    async def run_with_approval(
        self,
        goal: str,
        plan_callback=None,
    ) -> WorkflowResult:
        """
        Human-in-the-loop workflow: generate plan, get approval, then execute.

        Args:
            goal: The user's goal.
            plan_callback: Async callable that receives the plan and returns
                          True to approve or False to reject.

        Returns:
            WorkflowResult with the execution outcome.
        """
        start_time = time.time()

        # First, generate the plan
        plan_result = await self.run_plan_only(goal)
        if not plan_result.success:
            return plan_result

        # If callback provided, get approval
        if plan_callback:
            try:
                approved = await plan_callback(plan_result.result)
                if not approved:
                    return WorkflowResult(
                        success=False,
                        error="Plan rejected by user",
                        result=plan_result.result,
                        elapsed_time=time.time() - start_time,
                        workflow_type="approval",
                    )
            except Exception as e:
                return WorkflowResult(
                    success=False,
                    error=f"Approval callback failed: {e}",
                    elapsed_time=time.time() - start_time,
                    workflow_type="approval",
                )

        # Execute the approved plan
        execution_result = await self.run_standard(goal=goal)
        execution_result.workflow_type = "approval"
        execution_result.elapsed_time = time.time() - start_time
        return execution_result
