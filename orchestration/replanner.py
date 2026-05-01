"""
TAOS Replanner — Dynamic replanning on execution failure.

When the Reflector determines that the current plan is failing
(low confidence, repeated errors), the Replanner generates a new
plan incorporating lessons learned from the failed execution.

Production features:
- Context-aware replanning with failure history
- Remaining-steps-only replanning (skip completed steps)
- Replan budget tracking
- Plan validation before accepting new plan

PRD Reference: §22
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from taos.config.settings import get_settings
from taos.core.planner.planner import Planner, PlannerError
from taos.core.planner.plan_validator import PlanValidator
from taos.core.state.state_schema import GlobalState, PlanObject


class ReplanError(Exception):
    """Raised when replanning fails."""
    pass


class Replanner:
    """
    Production replanner.

    Generates a new execution plan when the current plan is
    failing, incorporating context from previous execution attempts.
    """

    def __init__(
        self,
        planner: Planner,
        plan_validator: PlanValidator,
    ) -> None:
        self._planner = planner
        self._plan_validator = plan_validator
        self._settings = get_settings()

    async def replan(
        self,
        state: GlobalState,
        context: Optional[List[str]] = None,
    ) -> PlanObject:
        """
        Generate a new plan based on current state and failure context.

        Builds an enriched context that includes:
        - The original goal
        - What steps were completed
        - What failed and why
        - Suggestions from reflection

        Returns:
            A validated PlanObject for the remaining work.

        Raises:
            ReplanError: If replanning fails or produces an invalid plan.
        """
        # ─── Check replan budget ───
        if state.replan_count >= self._settings.max_replans:
            raise ReplanError(
                f"Replan budget exhausted: {state.replan_count}/{self._settings.max_replans}"
            )

        # ─── Build enriched context ───
        enriched_context = self._build_replan_context(state, context or [])

        # ─── Remaining budget ───
        remaining_budget = max(
            self._settings.cost_budget_per_task - state.cost,
            0.01,  # minimum budget to avoid zero
        )
        remaining_steps = max(
            self._settings.max_steps - state.step,
            2,  # minimum steps for a useful plan
        )

        # ─── Generate new plan ───
        try:
            new_plan, planning_cost = await self._planner.generate_plan(
                goal=state.goal,
                context=enriched_context,
                max_steps=remaining_steps,
                cost_budget=remaining_budget,
            )
        except PlannerError as e:
            raise ReplanError(f"Failed to generate new plan: {e}")

        # ─── Validate new plan ───
        validation = self._plan_validator.validate(new_plan)
        if not validation.is_valid:
            raise ReplanError(
                f"Replanned plan is invalid: {'; '.join(validation.errors)}"
            )

        return new_plan

    def _build_replan_context(
        self,
        state: GlobalState,
        additional_context: List[str],
    ) -> List[str]:
        """
        Build context for the replanning LLM call.

        Includes information about what worked, what didn't,
        and why we're replanning.
        """
        context_parts: List[str] = []

        # 1. Replan indicator
        context_parts.append(
            f"[REPLANNING] This is replan attempt {state.replan_count + 1}/{self._settings.max_replans}. "
            f"The previous plan failed or had low confidence."
        )

        # 2. Completed steps summary
        completed_steps = []
        failed_steps = []
        for sr in state.step_results:
            if sr.success:
                completed_steps.append(
                    f"  ✓ Step {sr.step_id}: {sr.tool_name or 'reasoning'} — succeeded"
                )
            else:
                failed_steps.append(
                    f"  ✗ Step {sr.step_id}: {sr.tool_name or 'reasoning'} — FAILED: {sr.error}"
                )

        if completed_steps:
            context_parts.append(
                "[Completed steps (do NOT repeat these)]:\n" + "\n".join(completed_steps)
            )

        if failed_steps:
            context_parts.append(
                "[Failed steps (try a DIFFERENT approach)]:\n" + "\n".join(failed_steps)
            )

        # 3. Execution context
        if state.context:
            recent_context = state.context[-5:]
            context_parts.append(
                "[Recent execution context]:\n" + "\n".join(f"  - {c}" for c in recent_context)
            )

        # 4. Additional context (failure reasons, reflections)
        if additional_context:
            context_parts.extend(additional_context)

        # 5. Budget constraints
        remaining_cost = self._settings.cost_budget_per_task - state.cost
        context_parts.append(
            f"[Constraints] Remaining budget: ${remaining_cost:.4f}, "
            f"remaining steps: {self._settings.max_steps - state.step}, "
            f"confidence: {state.confidence:.2f}"
        )

        return context_parts
