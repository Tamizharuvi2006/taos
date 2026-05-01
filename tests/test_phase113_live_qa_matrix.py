from __future__ import annotations

from pathlib import Path

from taos.scripts.run_full_live_qa_matrix import (
    QACase,
    load_cases,
    mock_payload,
    no_raw_internal_error,
    run_matrix,
    validate_case,
    write_report,
)


def _case(case_id: str) -> QACase:
    cases = load_cases(Path("qa/live_qa_cases.json"))
    for case in cases:
        if case.id == case_id:
            return case
    raise AssertionError(f"Missing QA case: {case_id}")


def test_live_qa_cases_cover_required_routes() -> None:
    cases = load_cases(Path("qa/live_qa_cases.json"))
    ids = {case.id for case in cases}
    assert {
        "fast_message_greeting",
        "no_search_explain",
        "package_vite_typo",
        "package_react",
        "official_firebase",
        "news_openai_models",
        "comparison_rag_vector_db",
        "clarification_ambiguous",
    }.issubset(ids)

    routes = {case.expected_route for case in cases}
    assert {
        "fast_message",
        "no_search",
        "fast_search",
        "official_search",
        "news_search",
        "comparison_search",
        "clarification",
    }.issubset(routes)


def test_mock_matrix_passes_and_writes_reports(tmp_path: Path) -> None:
    cases = load_cases(Path("qa/live_qa_cases.json"))
    report = run_matrix(cases=cases, live=False)

    assert report["mode"] == "mock"
    assert report["case_count"] == len(cases)
    assert report["failed_count"] == 0
    assert report["passed_count"] == len(cases)

    json_path = tmp_path / "qa.json"
    md_path = tmp_path / "qa.md"
    write_report(report, json_path=json_path, md_path=md_path)

    assert json_path.exists()
    assert md_path.exists()
    assert "Full Live QA Matrix" in md_path.read_text(encoding="utf-8")


def test_package_case_enforces_source_of_record_and_no_generic_web() -> None:
    case = _case("package_vite_typo")
    status_code, payload, latency_ms = mock_payload(case)
    result = validate_case(case, payload, status_code=status_code, latency_ms=latency_ms)

    assert result.passed
    assert result.checks["source_of_record_used"]
    assert result.checks["generic_web_used"]
    assert result.checks["provider_health_present"]


def test_research_case_checks_coverage_answer_mode_and_confidence_reason() -> None:
    case = _case("official_firebase")
    status_code, payload, latency_ms = mock_payload(case)
    result = validate_case(case, payload, status_code=status_code, latency_ms=latency_ms)

    assert result.passed
    assert result.checks["sources_count_min"]
    assert result.checks["coverage_min"]
    assert result.checks["answer_mode_present"]
    assert result.checks["confidence_reason_present"]
    assert result.checks["unsupported_critical_claims"]


def test_mock_mode_is_default_and_does_not_need_live_base_url() -> None:
    case = _case("fast_message_greeting")
    report = run_matrix(cases=[case], live=False, base_url="http://127.0.0.1:1")

    assert report["mode"] == "mock"
    assert report["passed_count"] == 1
    assert report["failed_count"] == 0


def test_max_cases_is_enforced() -> None:
    cases = load_cases(Path("qa/live_qa_cases.json"))
    report = run_matrix(cases=cases, live=False, max_cases=3)

    assert report["case_count"] == 3
    assert len(report["results"]) == 3


def test_missing_optional_fixture_skips_safely() -> None:
    case = QACase(
        id="doc_fixture_missing",
        query="summarize the uploaded document",
        expected_route="doc_mode",
        expected_owner="document_pipeline",
        optional_fixture="fixtures/does-not-exist.pdf",
    )

    report = run_matrix(cases=[case], live=False)
    result = report["results"][0]

    assert result["skipped"] is True
    assert result["passed"] is True
    assert "Missing optional fixture" in result["skip_reason"]


def test_raw_internal_errors_are_detected() -> None:
    assert no_raw_internal_error({"answer": "Traceback (most recent call last): boom"}) is False
    assert no_raw_internal_error({"answer": "Answer\nThe best supported answer is ready."}) is True
