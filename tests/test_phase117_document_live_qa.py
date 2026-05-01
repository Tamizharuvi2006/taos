from __future__ import annotations

from pathlib import Path

from taos.scripts.run_document_live_qa import (
    DocumentQACase,
    load_cases,
    run_cases,
    validate_case,
    write_report,
)


def test_document_qa_case_loader_works() -> None:
    cases = load_cases(Path("qa/document_qa_cases.json"))

    assert len(cases) >= 4
    assert {case.mode for case in cases} >= {"general_doc_assist", "important_questions"}


def test_missing_fixtures_are_skipped_safely() -> None:
    case = DocumentQACase(
        id="missing_doc",
        file="qa/fixtures/missing.pdf",
        question="summarize",
    )
    report = run_cases([case], live=False)

    assert report["skipped_count"] == 1
    assert report["results"][0]["passed"] is True
    assert "Missing fixture" in report["results"][0]["skip_reason"]


def test_mock_document_qa_report_builds(tmp_path: Path) -> None:
    report = run_cases(load_cases(Path("qa/document_qa_cases.json")), live=False)
    json_path = tmp_path / "doc.json"
    md_path = tmp_path / "doc.md"
    write_report(report, json_path=json_path, md_path=md_path)

    assert report["failed_count"] == 0
    assert json_path.exists()
    assert "Document QA Matrix" in md_path.read_text(encoding="utf-8")


def test_answer_includes_document_source_chunk_info() -> None:
    case = load_cases(Path("qa/document_qa_cases.json"))[0]
    report = run_cases([case], live=False)
    checks = report["results"][0]["checks"]

    assert checks["source_chunk_present"] is True
    assert checks["metadata_present"] is True


def test_unknown_answer_does_not_hallucinate() -> None:
    case = next(case for case in load_cases(Path("qa/document_qa_cases.json")) if case.expect_uncertain)
    report = run_cases([case], live=False)
    result = report["results"][0]

    assert result["checks"]["unknown_answer_uncertain"] is True
    assert result["checks"]["unsupported_critical_zero"] is True
    assert "does not provide" in result["answer_preview"].lower()


def test_important_questions_mode_returns_exam_style_structure() -> None:
    case = next(case for case in load_cases(Path("qa/document_qa_cases.json")) if case.mode == "important_questions")
    report = run_cases([case], live=False)

    assert report["results"][0]["checks"]["important_questions_structure"] is True


def test_document_cache_metadata_preserved() -> None:
    case = next(case for case in load_cases(Path("qa/document_qa_cases.json")) if case.id == "doc_general_summary")
    report = run_cases([case], live=False)

    assert report["results"][0]["checks"]["cache_metadata_present"] is True
