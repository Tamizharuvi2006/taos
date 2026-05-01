from __future__ import annotations

from scripts.run_entity_discovery_qa import DEFAULT_CASES, load_cases, render_markdown, run_cases


def test_phase136_cases_load() -> None:
    cases = load_cases(DEFAULT_CASES)
    assert len(cases) == 5
    assert cases[0].expected_intent == "ceo_lookup"


def test_phase136_mock_runner_passes() -> None:
    report = run_cases(load_cases(DEFAULT_CASES), live=False)
    assert report["failed"] == 0
    assert report["passed"] == 5


def test_phase136_ceo_case_has_required_lanes() -> None:
    row = run_cases(load_cases(DEFAULT_CASES, max_cases=1), live=False)["results"][0]
    assert row["intent"] == "ceo_lookup"
    assert {"official_website", "linkedin", "news_articles", "registry_directory"} <= set(row["source_lanes"])


def test_phase136_profile_cases_have_social_and_linkedin_lanes() -> None:
    rows = {row["id"]: row for row in run_cases(load_cases(DEFAULT_CASES), live=False)["results"]}
    assert "social_profiles" in rows["instagram_profile_company"]["source_lanes"]
    assert "linkedin" in rows["linkedin_company_profile"]["source_lanes"]


def test_phase136_report_renders() -> None:
    report = run_cases(load_cases(DEFAULT_CASES, max_cases=2), live=False)
    markdown = render_markdown(report)
    assert "# Phase 136 Entity Discovery QA" in markdown
    assert "ceo_lookup_local_company" in markdown
