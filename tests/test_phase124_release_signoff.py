from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from taos.scripts import run_release_signoff as signoff


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def workspace_dir() -> Path:
    base = Path("artifacts/runtime_dirs/tmp_phase124_test_artifacts")
    base.mkdir(parents=True, exist_ok=True)
    target = base / str(uuid4())
    target.mkdir(parents=True, exist_ok=True)
    try:
        yield target
    finally:
        shutil.rmtree(target, ignore_errors=True)


def _seed_release_workspace(root: Path, *, rc_package_text: str, include_live_qa: bool = True, include_safety_line: bool = True) -> None:
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)

    _write(
        docs / "RC_FREEZE_CHECKLIST.md",
        (
            "# RC Freeze\n\n"
            "Blocked/warning classification:\n"
            "- Release blocker:\n"
            "  - none open\n\n"
            "## Exit Criteria\n"
            "- [x] All blockers resolved or consciously deferred with sign-off.\n"
        ),
    )
    _write(docs / "RC_RELEASE_PACKAGE.md", rc_package_text)
    _write(
        docs / "PRODUCTION_RELEASE_SIGNOFF.md",
        (
            "# Sign-off\n\n"
            "Frontend build:\n"
            "- cd D:\\agent\\frontend\n"
            "- npm run build\n\n"
            "Production safety:\n"
            + ("- AUTH_ALLOW_DEV_BYPASS=false\n" if include_safety_line else "")
            + "- Do not run dev perf bypass in production.\n"
        ),
    )
    _write(docs / "POST_DEPLOY_VERIFICATION.md", "# Post Deploy\n")
    _write(
        docs / "RELEASE_NOTES.md",
        (
            "# Release Notes\n\n"
            "## Major Completed Phases\n"
            "- Phase 123B: Rate-limit aware perf gate closure.\n"
            "- Phase 124: Release sign-off and deployment execution checklist.\n\n"
            "## Known Warnings\n"
            "- See docs/KNOWN_WARNINGS.md.\n"
        ),
    )
    _write(docs / "KNOWN_WARNINGS.md", "# Known Warnings\n\n- Example warning.\n")
    _write(docs / "ROLLBACK_PLAN.md", "# Rollback\n\n- Backend rollback\n- Frontend rollback\n")
    _write(docs / "ops_dashboard_latest.json", json.dumps({"ok": True}, indent=2))

    if include_live_qa:
        _write(root / "QA_RESULTS_LIVE_FULL.json", json.dumps({"ok": True}, indent=2))
    _write(root / "QA_RESULTS_DEPLOYMENT_SMOKE.json", json.dumps({"ok": True}, indent=2))
    _write(root / "QA_RESULTS_DOCUMENT.json", json.dumps({"ok": True}, indent=2))
    _write(root / "QA_RESULTS_PERSISTENCE.json", json.dumps({"ok": True}, indent=2))
    _write(root / "PERFORMANCE_RESULTS.json", json.dumps({"ok": True}, indent=2))


def _check_map(report: dict) -> dict[str, dict]:
    return {str(row.get("name")): row for row in list(report.get("checks") or [])}


def test_signoff_fails_when_rc_package_says_hold(workspace_dir: Path) -> None:
    _seed_release_workspace(
        workspace_dir,
        rc_package_text=(
            "# RC Package\n\n"
            "Status: HOLD\n"
            "Release blocker: performance reliability not closed.\n"
        ),
    )
    report = signoff.build_report(mock=True, repo_root=workspace_dir)
    checks = _check_map(report)

    assert report["ok"] is False
    assert checks["rc_status_ready_for_signoff"]["passed"] is False


def test_signoff_passes_when_rc_package_is_ready_for_signoff(workspace_dir: Path) -> None:
    _seed_release_workspace(
        workspace_dir,
        rc_package_text=(
            "# RC Package\n\n"
            "RC package is technically ready for final sign-off.\n"
            "No open technical release blockers after Phase 123B closure.\n"
        ),
    )
    report = signoff.build_report(mock=True, repo_root=workspace_dir)
    checks = _check_map(report)

    assert report["ok"] is True
    assert checks["rc_status_ready_for_signoff"]["passed"] is True


def test_missing_critical_artifact_fails_signoff(workspace_dir: Path) -> None:
    _seed_release_workspace(
        workspace_dir,
        rc_package_text=(
            "# RC Package\n\n"
            "RC package is technically ready for final sign-off.\n"
        ),
        include_live_qa=False,
    )
    report = signoff.build_report(mock=True, repo_root=workspace_dir)
    checks = _check_map(report)

    assert report["ok"] is False
    assert checks["qa_live_full_json_exists"]["passed"] is False


def test_known_warnings_are_included(workspace_dir: Path) -> None:
    _seed_release_workspace(
        workspace_dir,
        rc_package_text=(
            "# RC Package\n\n"
            "RC package is technically ready for final sign-off.\n"
        ),
    )
    report = signoff.build_report(mock=True, repo_root=workspace_dir)
    checks = _check_map(report)

    assert checks["known_warnings_exists"]["passed"] is True
    assert checks["release_notes_phase_summary_complete"]["passed"] is True


def test_production_safety_checklist_includes_auth_bypass_false(workspace_dir: Path) -> None:
    _seed_release_workspace(
        workspace_dir,
        rc_package_text=(
            "# RC Package\n\n"
            "RC package is technically ready for final sign-off.\n"
        ),
        include_safety_line=False,
    )
    report = signoff.build_report(mock=True, repo_root=workspace_dir)
    checks = _check_map(report)

    assert checks["production_safety_checklist_documented"]["passed"] is False


def test_release_notes_contain_major_completed_phase_summary(workspace_dir: Path) -> None:
    _seed_release_workspace(
        workspace_dir,
        rc_package_text=(
            "# RC Package\n\n"
            "RC package is technically ready for final sign-off.\n"
        ),
    )
    report = signoff.build_report(mock=True, repo_root=workspace_dir)
    checks = _check_map(report)

    assert checks["release_notes_phase_summary_complete"]["passed"] is True
