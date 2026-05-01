from __future__ import annotations

from taos.core.planner.prompt_templates import build_prompts, select_template


def test_template_selection_for_comparison():
    tpl = select_template("comparison")
    assert tpl.name == "comparison"


def test_template_selection_for_transform_and_definition():
    assert select_template("transform").name == "transform"
    assert select_template("definition").name == "transform"


def test_build_prompts_includes_hints_and_constraints():
    name, system_prompt, user_prompt = build_prompts(
        goal="Compare model A vs model B",
        context="prior context",
        available_tools=["web_search", "http_request"],
        max_steps=5,
        intent="comparison",
        extra_hints=["Use metric-based evaluation"],
    )
    assert name == "comparison"
    assert "Rules:" in system_prompt
    assert "Maximum steps: 5" in system_prompt
    assert "Use metric-based evaluation" in user_prompt
