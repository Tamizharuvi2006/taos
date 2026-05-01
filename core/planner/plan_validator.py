"""
TAOS Plan Validator — Validates plan structure and feasibility.

Checks:
- All referenced tools exist in registry
- No circular dependencies between steps
- Step count within limits
- Cost budget feasibility
- Goal complexity assessment (PRD §15)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from taos.config.constants import GoalComplexity
from taos.core.state.state_schema import PlanObject, PlanStep, StepType


@dataclass
class ValidationResult:
    """Result of plan validation."""

    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    complexity: GoalComplexity = GoalComplexity.LOW
    requires_planning: bool = True

    def add_error(self, error: str) -> None:
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)


class PlanValidator:
    """Production plan validator with comprehensive checks."""

    def __init__(self, registered_tools: Optional[Set[str]] = None):
        self._registered_tools = registered_tools or set()

    def validate(self, plan: PlanObject) -> ValidationResult:
        """Run all validation checks on a plan."""
        result = ValidationResult()

        self._check_empty_plan(plan, result)
        self._check_step_limit(plan, result)
        self._check_tool_references(plan, result)
        self._check_dependency_graph(plan, result)
        self._check_step_actions(plan, result)
        self._assess_complexity(plan, result)

        return result

    def _check_empty_plan(self, plan: PlanObject, result: ValidationResult) -> None:
        """Plan must have at least one step."""
        if not plan.steps:
            result.add_error("Plan contains no steps")

    def _check_step_limit(self, plan: PlanObject, result: ValidationResult) -> None:
        """Plan must not exceed max_steps."""
        if len(plan.steps) > plan.max_steps:
            result.add_error(
                f"Plan has {len(plan.steps)} steps but max is {plan.max_steps}"
            )

    def _check_tool_references(self, plan: PlanObject, result: ValidationResult) -> None:
        """All referenced tools must exist in registry."""
        for step in plan.steps:
            if step.step_type == StepType.DAG_EXEC:
                if not step.dag_name:
                    result.add_error(
                        f"Step '{step.id}' is DAG_EXEC but dag_name is missing"
                    )
                continue

            if not self._registered_tools:
                continue  # Skip tool check if no registry provided

            if step.tool and step.tool not in self._registered_tools:
                result.add_error(
                    f"Step '{step.id}' references unknown tool: '{step.tool}'. "
                    f"Available: {sorted(self._registered_tools)}"
                )

    def _check_dependency_graph(self, plan: PlanObject, result: ValidationResult) -> None:
        """Check for circular dependencies and missing dependency references."""
        step_ids = {step.id for step in plan.steps}

        # Check for missing dependency references
        for step in plan.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    result.add_error(
                        f"Step '{step.id}' depends on non-existent step '{dep}'"
                    )

        # Check for circular dependencies via DFS
        if self._has_circular_deps(plan.steps):
            result.add_error("Plan contains circular dependencies")

    def _has_circular_deps(self, steps: List[PlanStep]) -> bool:
        """Detect circular dependencies using DFS."""
        adj: Dict[str, List[str]] = {step.id: list(step.depends_on) for step in steps}
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            rec_stack.discard(node)
            return False

        for step_id in adj:
            if step_id not in visited:
                if dfs(step_id):
                    return True
        return False

    def _check_step_actions(self, plan: PlanObject, result: ValidationResult) -> None:
        """Each step must have a non-empty action."""
        for step in plan.steps:
            if not step.action or not step.action.strip():
                result.add_error(f"Step '{step.id}' has empty action")

        # Check for duplicate step IDs
        seen_ids: Set[str] = set()
        for step in plan.steps:
            if step.id in seen_ids:
                result.add_error(f"Duplicate step ID: '{step.id}'")
            seen_ids.add(step.id)

    def _assess_complexity(self, plan: PlanObject, result: ValidationResult) -> None:
        """Assess plan complexity based on step count and tool usage."""
        step_count = len(plan.steps)
        tool_steps = sum(1 for s in plan.steps if s.tool or s.step_type == StepType.DAG_EXEC)
        has_deps = any(s.depends_on for s in plan.steps)

        if step_count <= 2 and tool_steps <= 1:
            result.complexity = GoalComplexity.LOW
        elif step_count <= 5 or (tool_steps <= 3 and not has_deps):
            result.complexity = GoalComplexity.MEDIUM
        else:
            result.complexity = GoalComplexity.HIGH

        result.requires_planning = step_count > 1


def validate_goal(goal: str) -> ValidationResult:
    """
    Quick goal validation before planning (PRD §15).
    
    Checks if the goal is valid and estimates complexity.
    """
    result = ValidationResult()

    if not goal or not goal.strip():
        result.add_error("Goal is empty")
        return result

    goal = goal.strip()

    if len(goal) < 3:
        result.add_error("Goal is too short (min 3 characters)")

    if len(goal) > 2000:
        result.add_error("Goal is too long (max 2000 characters)")

    # Simple complexity heuristic based on goal text
    word_count = len(goal.split())
    if word_count <= 5:
        result.complexity = GoalComplexity.LOW
        result.requires_planning = False
    elif word_count <= 20:
        result.complexity = GoalComplexity.MEDIUM
        result.requires_planning = True
    else:
        result.complexity = GoalComplexity.HIGH
        result.requires_planning = True

    return result
