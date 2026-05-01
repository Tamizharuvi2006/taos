from __future__ import annotations

from scripts.run_universal_understanding_qa import (
    DEFAULT_CASES,
    UniversalUnderstandingCase,
    load_cases,
    render_markdown,
    run_cases,
    validate_case,
)


def test_phase134_cases_load() -> None:
    cases = load_cases(DEFAULT_CASES)
    assert len(cases) == 9
    assert cases[0].id == "fast_message_messy"
    assert cases[-1].expected_route == "clarification"


def test_phase134_mock_contract_passes_all_cases() -> None:
    report = run_cases(load_cases(DEFAULT_CASES), live=False)
    assert report["failed"] == 0
    assert report["passed"] == 9


def test_phase134_original_and_normalized_query_are_preserved() -> None:
    case = [item for item in load_cases(DEFAULT_CASES) if item.id == "no_search_messy"][0]
    row = run_cases([case], live=False)["results"][0]
    assert row["checks"]["original_query_preserved"] is True
    assert row["checks"]["normalized_query_present"] is True
    assert row["normalized_query"] == "explain JavaScript closures simply"


def test_phase134_package_source_of_record_contract() -> None:
    case = [item for item in load_cases(DEFAULT_CASES) if item.id == "package_typo"][0]
    row = run_cases([case], live=False)["results"][0]
    assert row["route"] == "fast_search"
    assert row["checks"]["source_of_record_used"] is True
    assert row["checks"]["generic_web_used_false"] is True
    assert "Vite" in row["entities"]


def test_phase134_research_query_plan_contract() -> None:
    case = [item for item in load_cases(DEFAULT_CASES) if item.id == "rumour_research"][0]
    row = run_cases([case], live=False)["results"][0]
    plan = row["query_plan_summary"]
    assert row["route"] == "news_search"
    assert row["owner"] == "research_pipeline"
    assert plan["lanes"]["official"]
    assert row["checks"]["raw_query_not_primary"] is True


def test_phase134_doc_task_code_hints_present() -> None:
    cases = {case.id: case for case in load_cases(DEFAULT_CASES)}
    report = run_cases([cases["doc_exam_messy"], cases["task_reminder_messy"], cases["code_help_messy"]], live=False)
    rows = {row["id"]: row for row in report["results"]}
    assert rows["doc_exam_messy"]["checks"]["document_mode_hint_present"] is True
    assert rows["doc_exam_messy"]["checks"]["document_mark_format_present"] is True
    assert rows["task_reminder_messy"]["checks"]["task_hint_present"] is True
    assert rows["code_help_messy"]["checks"]["code_hint_present"] is True


def test_phase134_comparison_entities_include_corrections() -> None:
    case = [item for item in load_cases(DEFAULT_CASES) if item.id == "comparison_messy"][0]
    row = run_cases([case], live=False)["results"][0]
    assert row["route"] == "comparison_search"
    assert "React" in row["entities"]
    assert "Angular" in row["entities"]


def test_phase134_low_signal_routes_clarification() -> None:
    case = [item for item in load_cases(DEFAULT_CASES) if item.id == "low_signal_ambiguity"][0]
    row = run_cases([case], live=False)["results"][0]
    assert row["route"] == "clarification"
    assert row["owner"] == "clarification"
    assert row["checks"]["route_matches"] is True


def test_phase134_validation_detects_missing_intent_frame() -> None:
    case = UniversalUnderstandingCase(id="bad", query="heyy buddyy", expected_route="fast_message", expected_owner="direct")
    result = validate_case(case, payload={"answer": "hello", "route": "fast_message", "trace": {"route_label": "fast_message"}})
    assert result["ok"] is False
    assert "intent_frame_present" in result["failed_checks"]
    assert "normalized_query_present" in result["failed_checks"]


def test_phase134_markdown_renders() -> None:
    report = run_cases(load_cases(DEFAULT_CASES, max_cases=2), live=False)
    markdown = render_markdown(report)
    assert "# Phase 134 Universal Understanding QA" in markdown
    assert "fast_message_messy" in markdown
    assert "| Case | Route | Owner | Route hint | OK | Failed checks | Normalized query |" in markdown
