"""Autonomous goal decomposition for complex tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from taos.core.state.state_schema import PlanObject, PlanStep


@dataclass
class DecompositionResult:
    should_decompose: bool
    subgoals: List[str]
    reason: str = ""


class GoalDecomposer:
    """Rule-guided decomposer to split large goals into bounded subgoals."""

    def __init__(self, max_subgoals: int = 5) -> None:
        self._max_subgoals = max(2, max_subgoals)

    def analyze(self, goal: str, intent: Optional[str]) -> DecompositionResult:
        normalized = (goal or "").strip()
        if len(normalized) < 80 and " and " not in normalized.lower():
            return DecompositionResult(False, [], "goal_simple")

        intent_key = (intent or "").lower()
        if intent_key in {"transform", "definition", "simple_lookup"}:
            return DecompositionResult(False, [], "intent_fast_path")

        parts = self._split_goal(normalized)
        if len(parts) <= 1:
            return DecompositionResult(False, [], "single_segment")

        return DecompositionResult(
            should_decompose=True,
            subgoals=parts[: self._max_subgoals],
            reason="multi_clause_complex_goal",
        )

    def build_subgoal_hints(self, subgoals: List[str]) -> List[str]:
        hints: List[str] = []
        for idx, subgoal in enumerate(subgoals, start=1):
            hints.append(f"[Subgoal {idx}] {subgoal}")
        return hints

    def merge_subplans(
        self,
        subgoal_plans: List[tuple[str, PlanObject]],
        max_steps: int,
        cost_budget: float,
    ) -> PlanObject:
        """
        Merge mini-plans into one global plan with safe sequential dependencies.
        """
        merged_steps: List[PlanStep] = []
        previous_tail: Optional[str] = None
        local_counter = 1

        for subgoal_index, (_, plan) in enumerate(subgoal_plans, start=1):
            id_map: dict[str, str] = {}
            for step in plan.steps:
                new_id = f"sg{subgoal_index}_{local_counter}"
                local_counter += 1
                id_map[step.id] = new_id

            for step in plan.steps:
                rewritten_deps = [id_map[d] for d in step.depends_on if d in id_map]
                if previous_tail and not rewritten_deps:
                    rewritten_deps = [previous_tail]
                merged_steps.append(
                    step.model_copy(
                        update={
                            "id": id_map[step.id],
                            "depends_on": rewritten_deps,
                        }
                    )
                )

            if merged_steps:
                previous_tail = merged_steps[-1].id

        if len(merged_steps) > max_steps:
            merged_steps = merged_steps[:max_steps]

        return PlanObject(
            steps=merged_steps,
            max_steps=max_steps,
            cost_budget=cost_budget,
        )

    def _split_goal(self, goal: str) -> List[str]:
        # Split on common orchestration conjunctions.
        chunks = re.split(
            r"\b(?:and then|then|after that|also|and|,)\b",
            goal,
            flags=re.IGNORECASE,
        )
        normalized = [c.strip(" .;:\t") for c in chunks if c and c.strip(" .;:\t")]
        # remove near-duplicates while preserving order
        seen: set[str] = set()
        unique: List[str] = []
        for item in normalized:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique
