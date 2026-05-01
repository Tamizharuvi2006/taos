from __future__ import annotations

from pathlib import Path

from taos.scripts import release_preflight


REQUIRED_DOCS = [
    Path("docs/RUNBOOK.md"),
    Path("docs/RELEASE_CHECKLIST.md"),
    Path("docs/ROLLBACK_PLAN.md"),
    Path("docs/INCIDENT_RESPONSE.md"),
    Path("docs/KNOWN_WARNINGS.md"),
]

REQUIRED_COMMANDS = [
    "python scripts/run_deployment_smoke.py --mock",
    "python scripts/run_deployment_smoke.py --live --base-url http://localhost:8000 --dev-user-id <id>",
    "python scripts/run_full_live_qa_matrix.py --mock",
    "python scripts/run_full_live_qa_matrix.py --live --base-url http://localhost:8000 --max-cases 8",
    "python scripts/run_document_live_qa.py --mock",
    "python scripts/run_document_live_qa.py --live --base-url http://localhost:8000 --max-cases 3",
    "python scripts/build_ops_dashboard.py",
    "python scripts/run_research_eval.py --mock",
    "cd D:\\agent\\frontend",
    "npm run build",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase120_required_docs_exist() -> None:
    for doc in REQUIRED_DOCS:
        assert doc.exists(), f"Missing required Phase 120 doc: {doc}"


def test_release_preflight_mock_mode_builds_report() -> None:
    report = release_preflight.build_report(mock=True)

    assert report["mode"] == "mock"
    assert isinstance(report.get("checks"), list)
    assert any(check["name"] == "runbook_doc_exists" for check in report["checks"])


def test_required_live_qa_commands_are_documented() -> None:
    combined = "\n".join(_read(path) for path in [Path("docs/RUNBOOK.md"), Path("docs/RELEASE_CHECKLIST.md")])
    for command in REQUIRED_COMMANDS:
        assert command in combined, f"Missing runbook/release checklist command: {command}"


def test_rollback_includes_backend_and_frontend_paths() -> None:
    text = _read(Path("docs/ROLLBACK_PLAN.md")).lower()
    assert "backend rollback" in text
    assert "frontend rollback" in text


def test_known_warnings_include_python_and_firebase_alts_context() -> None:
    text = _read(Path("docs/KNOWN_WARNINGS.md")).lower()
    assert "python" in text and "3.13" in text
    assert "firebase" in text
    assert "alts" in text


def test_docs_explicitly_cover_mock_and_live_verification_and_optional_fixture_skips() -> None:
    runbook_text = _read(Path("docs/RUNBOOK.md")).lower()
    assert "--mock" in runbook_text
    assert "--live" in runbook_text
    assert "fixture" in runbook_text and "skip" in runbook_text
