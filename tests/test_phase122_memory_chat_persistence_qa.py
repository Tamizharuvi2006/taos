from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from taos.scripts.run_persistence_qa import (
    detect_cross_user_isolation_failure,
    load_cases,
    run_persistence_qa,
    write_report,
)


def test_persistence_case_loader_works() -> None:
    cases = load_cases(Path("qa/persistence_cases.json"))

    assert len(cases) >= 5
    assert {"chat_save_and_read", "trace_metadata_saved", "cross_user_isolation", "memory_fallback", "task_execution_history"} == {case.id for case in cases}


def test_mock_persistence_qa_report_builds() -> None:
    report = run_persistence_qa(cases=load_cases(Path("qa/persistence_cases.json")), live=False)

    assert report["mode"] == "mock"
    assert report["case_count"] == 5
    assert report["failed_count"] == 0


def test_chat_save_and_read_behavior_passes_in_mock_store() -> None:
    case = [case for case in load_cases(Path("qa/persistence_cases.json")) if case.id == "chat_save_and_read"]
    report = run_persistence_qa(cases=case, live=False)
    result = report["results"][0]

    assert result["passed"] is True
    assert result["checks"]["chat_saved"] is True
    assert result["checks"]["message_count_preserved"] is True


def test_trace_and_trust_metadata_persistence_is_validated() -> None:
    case = [case for case in load_cases(Path("qa/persistence_cases.json")) if case.id == "trace_metadata_saved"]
    report = run_persistence_qa(cases=case, live=False)
    result = report["results"][0]

    assert result["passed"] is True
    assert result["checks"]["trace_persisted"] is True
    assert result["checks"]["trust_block_persisted"] is True


def test_cross_user_isolation_failure_is_detected() -> None:
    assert detect_cross_user_isolation_failure(owner_can_read=True, other_can_read=True) is True
    assert detect_cross_user_isolation_failure(owner_can_read=True, other_can_read=False) is False


def test_firebase_unavailable_fallback_is_controlled() -> None:
    case = [case for case in load_cases(Path("qa/persistence_cases.json")) if case.id == "memory_fallback"]
    report = run_persistence_qa(cases=case, live=False)
    result = report["results"][0]

    assert result["checks"]["fallback_controlled"] is True


def _sandbox_dir() -> Path:
    root = Path("tmp_phase122_test_artifacts") / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_json_and_markdown_reports_are_generated() -> None:
    report = run_persistence_qa(cases=load_cases(Path("qa/persistence_cases.json")), live=False)
    out_root = _sandbox_dir()
    out_json = out_root / "persistence.json"
    out_md = out_root / "persistence.md"
    write_report(report, json_path=out_json, md_path=out_md)

    assert out_json.exists()
    assert out_md.exists()
    assert "Persistence QA" in out_md.read_text(encoding="utf-8")
    loaded = json.loads(out_json.read_text(encoding="utf-8"))
    assert loaded["mode"] == "mock"
