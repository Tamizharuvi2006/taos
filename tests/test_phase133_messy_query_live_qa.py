from __future__ import annotations


from scripts.run_messy_query_live_qa import (
    DEFAULT_CASES,
    MessyQueryCase,
    load_cases,
    render_markdown,
    run_cases,
    validate_case,
)


def test_phase133_cases_load() -> None:
    cases = load_cases(DEFAULT_CASES)
    assert len(cases) == 6
    assert cases[0].id == "india_claude_lovking_rumour"
    assert cases[-1].requires_source_of_record is True


def test_phase133_mock_run_passes_all_cases() -> None:
    report = run_cases(load_cases(DEFAULT_CASES), live=False)
    assert report["failed"] == 0
    assert report["passed"] == 6


def test_phase133_raw_typo_query_is_not_primary_search() -> None:
    report = run_cases(load_cases(DEFAULT_CASES, max_cases=1), live=False)
    row = report["results"][0]
    assert row["checks"]["raw_query_preserved"] is True
    assert row["checks"]["raw_query_not_primary"] is True
    assert row["primary_query"].lower() != row["query"].lower()


def test_phase133_query_plan_summary_has_required_lanes() -> None:
    report = run_cases(load_cases(DEFAULT_CASES, max_cases=1), live=False)
    row = report["results"][0]
    lanes = row["query_plan_summary"]["lanes"]
    assert lanes["official"]
    assert lanes["news"]
    assert lanes["contradiction"]
    assert lanes["background"]
    assert row["query_plan_summary"]["normalized_question"]


def test_phase133_rumour_answer_contains_status_and_useful_context() -> None:
    report = run_cases(load_cases(DEFAULT_CASES, max_cases=1), live=False)
    row = report["results"][0]
    answer = row["answer_preview"].lower()
    assert "rumour status" in answer
    assert "best-supported status" in answer
    assert "confusion" in answer or "related" in answer
    assert "i couldn't verify this confidently" not in answer


def test_phase133_package_typo_stays_source_of_record() -> None:
    package_case = [case for case in load_cases(DEFAULT_CASES) if case.id == "package_typo_guard"][0]
    report = run_cases([package_case], live=False)
    row = report["results"][0]
    assert row["ok"] is True
    assert row["route"] == "fast_search"
    assert row["checks"]["package_source_of_record_present"] is True
    assert "vite" in " ".join(row["query_plan_summary"]["lanes"].get("technical") or []).lower()


def test_phase133_validation_detects_generic_failure() -> None:
    case = MessyQueryCase(
        id="bad_generic",
        query="rumor openai blocked in india",
        expected_intent="rumour_verification",
        requires_rumour_status=True,
        requires_best_supported_status=True,
    )
    payload = {
        "answer": "I couldn't verify this confidently from reliable sources.",
        "query_plan_summary": {
            "original_query": case.query,
            "intent": "rumour_verification",
            "normalized_question": "Is OpenAI blocked in India?",
            "raw_query_priority": "fallback_only",
            "lanes": {
                "official": ["OpenAI supported countries India"],
                "news": ["OpenAI blocked India"],
                "contradiction": ["OpenAI available India"],
                "background": ["OpenAI outage India"],
                "fallback": [case.query],
            },
            "metadata": {"entities": {"company": "OpenAI", "country": "India"}, "relation": "blocked_or_restricted_access"},
        },
        "sources": [{"title": "Mock source"}],
    }
    result = validate_case(case, payload=payload)
    assert result["ok"] is False
    assert "not_generic_failure" in result["failed_checks"]


def test_phase133_markdown_report_renders() -> None:
    report = run_cases(load_cases(DEFAULT_CASES, max_cases=2), live=False)
    markdown = render_markdown(report)
    assert "# Phase 133 Messy Query Live QA" in markdown
    assert "india_claude_lovking_rumour" in markdown
    assert "| Case | Route | Intent | Relation | OK | Failed checks | Primary query |" in markdown
