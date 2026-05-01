from __future__ import annotations

from pathlib import Path

from scripts.run_research_killer_eval import load_cases, render_markdown, run_cases


def test_killer_eval_cases_include_messy_queries() -> None:
    cases = load_cases(Path("eval/research_killer_cases_v2.json"))
    assert any(case["category"] == "typo-heavy rumours" for case in cases)
    assert any("agala" in case["query"] for case in cases)


def test_killer_eval_fails_raw_typo_query_as_primary() -> None:
    report = run_cases([
        {
            "id": "messy",
            "query": "research that indai is lovking claudde rumour",
            "expected_intent": "rumour_verification",
            "forbid_primary_raw_query": True,
            "forbid_generic_no_result": True,
        }
    ])
    assert report["failed"] == 0
    first = report["results"][0]["query_plan"]
    flattened = []
    for values in first["lanes"].values():
        flattened.extend(values)
    assert flattened[0] != "research that indai is lovking claudde rumour"


def test_killer_eval_markdown_is_generated() -> None:
    report = run_cases(load_cases(Path("eval/research_killer_cases_v2.json"))[:1])
    md = render_markdown(report)
    assert "Research Killer Eval v2" in md
    assert "Pass rate" in md
