from __future__ import annotations

from pathlib import Path

from taos.scripts.run_memory_qa import JSON_REPORT, MD_REPORT, load_cases, run_mock, write_reports


def test_memory_qa_case_loader_works() -> None:
    cases = load_cases()
    ids = {case["id"] for case in cases}
    assert {"explicit_remember", "secret_not_saved", "forget_memory", "recall_project", "false_memory_guard"}.issubset(ids)


def test_mock_memory_qa_passes_and_generates_reports() -> None:
    report = run_mock()
    assert report["failed"] == 0
    assert report["passed"] == report["total"]
    write_reports(report)
    assert JSON_REPORT.exists()
    assert MD_REPORT.exists()
    assert "TAOS Memory QA Results" in MD_REPORT.read_text(encoding="utf-8")


def test_memory_qa_supports_max_cases() -> None:
    report = run_mock(max_cases=2)
    assert report["total"] == 2
    assert report["failed"] == 0
