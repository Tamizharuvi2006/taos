from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from taos.core.planner.planner import PlannerError
from taos.core.semantic.intent_classifier import IntentType
from taos.core.state.state_schema import GlobalState, PlanObject


@dataclass
class PlannerRunMetadata:
    goal: str
    intent: Optional[str] = None
    planner_hints_count: int = 0
    feedback_hints_count: int = 0
    tools_override: Optional[List[str]] = None
    decomposition_used: bool = False
    freshness_sensitive: bool = False
    planning_cost: float = 0.0
    validation_errors: List[str] = field(default_factory=list)


class PlannerOrchestrator:
    """Owns planner generation/validation while preserving engine behavior."""

    def build_tools_override(self, intent: Optional[str]) -> Optional[List[str]]:
        if intent in [IntentType.DEBUG.value, IntentType.DEFINITION.value]:
            return []
        if intent in [IntentType.RESEARCH.value, IntentType.NEWS.value]:
            return ["web_search", "web_extract"]
        return None

    async def generate_and_validate(
        self,
        *,
        engine: Any,
        state: GlobalState,
        goal: str,
        intent: Optional[str] = None,
        feedback_hints: Optional[List[str]] = None,
        planner_hints: Optional[List[str]] = None,
    ) -> tuple[GlobalState, PlanObject, PlannerRunMetadata]:
        context = engine._memory.build_context_window(state)
        if feedback_hints:
            context.extend(feedback_hints)
        if planner_hints:
            context.extend(planner_hints)

        tools_override = self.build_tools_override(intent)
        metadata = PlannerRunMetadata(
            goal=goal,
            intent=intent,
            planner_hints_count=len(planner_hints or []),
            feedback_hints_count=len(feedback_hints or []),
            tools_override=tools_override,
        )

        planning_cost = 0.0
        decomposition = engine._decomposer.analyze(goal=goal, intent=intent)
        freshness_sensitive = engine._is_freshness_sensitive_research(goal)
        metadata.freshness_sensitive = bool(freshness_sensitive)
        if (
            bool(engine._settings.goal_decomposition_enabled)
            and decomposition.should_decompose
            and not freshness_sensitive
        ):
            metadata.decomposition_used = True
            engine._log(
                "engine.goal_decomposition",
                reason=decomposition.reason,
                subgoals=decomposition.subgoals,
            )
            decomposition_hints = engine._decomposer.build_subgoal_hints(decomposition.subgoals)
            subgoal_plans: List[tuple[str, PlanObject]] = []
            max_steps = max(2, int(engine._settings.max_steps))
            per_subgoal_steps = max(1, max_steps // max(1, len(decomposition.subgoals)))
            for subgoal in decomposition.subgoals:
                subgoal_context = context + decomposition_hints + [f"[CurrentSubgoal] {subgoal}"]
                sub_plan, sub_cost = await engine._generate_plan_with_adapter(
                    goal=subgoal,
                    context=subgoal_context,
                    intent=intent,
                    available_tools_override=tools_override,
                    request_id=state.request_id,
                )
                sub_validation = engine._plan_validator.validate(sub_plan)
                if not sub_validation.is_valid:
                    metadata.validation_errors.extend(sub_validation.errors)
                    raise PlannerError(
                        f"Subgoal plan is invalid for '{subgoal}': " + "; ".join(sub_validation.errors)
                    )
                sub_plan = sub_plan.model_copy(update={"max_steps": per_subgoal_steps})
                subgoal_plans.append((subgoal, sub_plan))
                planning_cost += sub_cost
            plan = engine._decomposer.merge_subplans(
                subgoal_plans=subgoal_plans,
                max_steps=max_steps,
                cost_budget=float(engine._settings.cost_budget_per_task),
            )
        else:
            if freshness_sensitive and decomposition.should_decompose:
                engine._log(
                    "engine.goal_decomposition_skipped",
                    reason="freshness_sensitive_research",
                    goal=goal[:140],
                )
            plan, planning_cost = await engine._generate_plan_with_adapter(
                goal=goal,
                context=context,
                intent=intent,
                available_tools_override=tools_override,
                request_id=state.request_id,
            )

        validation = engine._plan_validator.validate(plan)
        if not validation.is_valid:
            metadata.validation_errors.extend(validation.errors)
            raise PlannerError("Generated plan is invalid: " + "; ".join(validation.errors))

        state = engine._controller.set_plan(state, plan)
        state = state.model_copy_with_version(
            cost=state.cost + planning_cost,
            cost_breakdown=state.cost_breakdown.model_copy(
                update={"planner_cost": state.cost_breakdown.planner_cost + planning_cost}
            ),
        )
        metadata.planning_cost = float(planning_cost or 0.0)
        return state, plan, metadata
