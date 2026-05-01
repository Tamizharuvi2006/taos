from __future__ import annotations

from taos.core.planner.decomposition import GoalDecomposer
from taos.core.state.state_schema import PlanObject, PlanStep


def test_decomposer_detects_multiclause_goal():
    decomposer = GoalDecomposer(max_subgoals=5)
    res = decomposer.analyze(
        goal="Build backend API and then build frontend dashboard and deploy to cloud",
        intent="task",
    )
    assert res.should_decompose is True
    assert len(res.subgoals) >= 2


def test_decomposer_skips_fast_intents():
    decomposer = GoalDecomposer(max_subgoals=5)
    res = decomposer.analyze(goal="What is Docker?", intent="definition")
    assert res.should_decompose is False


def test_merge_subplans_rewrites_dependencies():
    decomposer = GoalDecomposer(max_subgoals=5)
    p1 = PlanObject(steps=[PlanStep(id="a", action="search A", tool="web_search")])
    p2 = PlanObject(steps=[PlanStep(id="b", action="analyze B", tool=None)])
    merged = decomposer.merge_subplans(
        subgoal_plans=[("g1", p1), ("g2", p2)],
        max_steps=10,
        cost_budget=1.0,
    )
    assert len(merged.steps) == 2
    assert merged.steps[1].depends_on == [merged.steps[0].id]
