from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_PARENT = _REPO_ROOT.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))


@dataclass
class SignoffCheck:
    name: str
    passed: bool
    status: str = "passed"
    blocked: bool = False
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _make_check(
    name: str,
    passed: bool,
    *,
    detail: str = "",
    blocked: bool = False,
    data: Mapping[str, Any] | None = None,
) -> SignoffCheck:
    if passed:
        status = "passed"
    elif blocked:
        status = "blocked"
    else:
        status = "failed"
    return SignoffCheck(
        name=name,
        passed=bool(passed),
        status=status,
        blocked=bool(blocked),
        detail=detail,
        data=dict(data or {}),
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _required_paths(root: Path) -> dict[str, Path]:
    return {
        "rc_freeze_checklist": root / "docs" / "RC_FREEZE_CHECKLIST.md",
        "rc_release_package": root / "docs" / "RC_RELEASE_PACKAGE.md",
        "production_release_signoff": root / "docs" / "PRODUCTION_RELEASE_SIGNOFF.md",
        "post_deploy_verification": root / "docs" / "POST_DEPLOY_VERIFICATION.md",
        "release_notes": root / "docs" / "RELEASE_NOTES.md",
        "known_warnings": root / "docs" / "KNOWN_WARNINGS.md",
        "rollback_plan": root / "docs" / "ROLLBACK_PLAN.md",
        "ops_dashboard_json": root / "docs" / "ops_dashboard_latest.json",
        "qa_live_full_json": root / "QA_RESULTS_LIVE_FULL.json",
        "qa_deployment_smoke_json": root / "QA_RESULTS_DEPLOYMENT_SMOKE.json",
        "qa_document_json": root / "QA_RESULTS_DOCUMENT.json",
        "qa_persistence_json": root / "QA_RESULTS_PERSISTENCE.json",
    }


def _release_outputs_dir(root: Path) -> Path:
    return root / "artifacts" / "release_outputs"


def _resolve_artifact_path(primary_path: Path, root: Path) -> Path:
    alt = _release_outputs_dir(root) / primary_path.name
    if primary_path.exists():
        return primary_path
    if alt.exists():
        return alt
    return primary_path


def _artifact_exists(primary_path: Path, root: Path) -> bool:
    return _resolve_artifact_path(primary_path, root).exists()


def _rc_ready_for_signoff(rc_release_package_text: str) -> tuple[bool, dict[str, Any]]:
    text = str(rc_release_package_text or "")
    lower = text.lower()
    hold_detected = bool(re.search(r"\bhold\b", lower))
    ready_phrase_detected = any(
        phrase in lower
        for phrase in (
            "ready-for-signoff",
            "ready for signoff",
            "ready for sign-off",
            "technically ready for final sign-off",
            "technically ready for final signoff",
        )
    )
    no_open_blockers_detected = "no open technical release blockers" in lower
    ok = bool((ready_phrase_detected or no_open_blockers_detected) and not hold_detected)
    return ok, {
        "hold_detected": hold_detected,
        "ready_phrase_detected": ready_phrase_detected,
        "no_open_blockers_detected": no_open_blockers_detected,
    }


def _freeze_checklist_green(rc_freeze_checklist_text: str) -> tuple[bool, dict[str, Any]]:
    lower = str(rc_freeze_checklist_text or "").lower()
    blockers_line_checked = "[x] all blockers resolved" in lower
    blockers_line_unchecked = "[ ] all blockers resolved" in lower
    none_open_release_blocker = "release blocker" in lower and "none open" in lower
    ok = bool(blockers_line_checked and not blockers_line_unchecked and none_open_release_blocker)
    return ok, {
        "blockers_line_checked": blockers_line_checked,
        "blockers_line_unchecked": blockers_line_unchecked,
        "none_open_release_blocker": none_open_release_blocker,
    }


def _perf_artifacts_present(root: Path) -> tuple[bool, dict[str, Any]]:
    perf_primary = _resolve_artifact_path(root / "PERFORMANCE_RESULTS.json", root)
    live_paths = [
        _resolve_artifact_path(root / "PERFORMANCE_RESULTS_live_c1.json", root),
        _resolve_artifact_path(root / "PERFORMANCE_RESULTS_live_c2.json", root),
        _resolve_artifact_path(root / "PERFORMANCE_RESULTS_live_c5.json", root),
    ]
    live_all_present = all(path.exists() for path in live_paths)
    ok = bool(perf_primary.exists() or live_all_present)
    return ok, {
        "performance_results_json_exists": perf_primary.exists(),
        "live_perf_artifacts_present": live_all_present,
        "live_paths": [str(path) for path in live_paths],
    }


def _frontend_build_requirement_documented(signoff_doc_text: str) -> tuple[bool, dict[str, Any]]:
    lower = str(signoff_doc_text or "").lower()
    has_frontend_path = "d:\\agent\\frontend" in lower
    has_build_command = "npm run build" in lower
    return bool(has_frontend_path and has_build_command), {
        "frontend_path_documented": has_frontend_path,
        "build_command_documented": has_build_command,
    }


def _production_safety_documented(signoff_doc_text: str) -> tuple[bool, dict[str, Any]]:
    lower = str(signoff_doc_text or "").lower()
    has_bypass_disabled = "auth_allow_dev_bypass=false" in lower
    has_perf_bypass_rule = ("do not run dev perf bypass in production" in lower) or ("perf_test_mode" in lower and "production" in lower)
    return bool(has_bypass_disabled and has_perf_bypass_rule), {
        "auth_allow_dev_bypass_false": has_bypass_disabled,
        "perf_bypass_rule_documented": has_perf_bypass_rule,
    }


def _release_notes_summary_complete(release_notes_text: str) -> tuple[bool, dict[str, Any]]:
    lower = str(release_notes_text or "").lower()
    has_phase_123b = "phase 123b" in lower
    has_phase_124 = "phase 124" in lower
    has_known_warnings = "known warnings" in lower
    return bool(has_phase_123b and has_phase_124 and has_known_warnings), {
        "phase_123b": has_phase_123b,
        "phase_124": has_phase_124,
        "known_warnings": has_known_warnings,
    }


def _probe_health(base_url: str, timeout: float = 8.0) -> tuple[int | None, str]:
    url = f"{str(base_url).rstrip('/')}/health"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return int(response.status), ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), ""
    except Exception as exc:
        return None, str(exc)


def build_report(
    *,
    mock: bool,
    base_url: str = "http://localhost:8000",
    repo_root: Path | None = None,
) -> dict[str, Any]:
    root = repo_root or _REPO_ROOT
    paths = _required_paths(root)
    checks: list[SignoffCheck] = []

    for name, path in paths.items():
        is_release_artifact = name in {
            "qa_live_full_json",
            "qa_deployment_smoke_json",
            "qa_document_json",
            "qa_persistence_json",
        }
        resolved = _resolve_artifact_path(path, root) if is_release_artifact else path
        exists = _artifact_exists(path, root) if is_release_artifact else path.exists()
        checks.append(
            _make_check(
                f"{name}_exists",
                exists,
                detail=str(resolved),
            )
        )

    rc_freeze_text = _read_text(paths["rc_freeze_checklist"])
    rc_release_text = _read_text(paths["rc_release_package"])
    signoff_doc_text = _read_text(paths["production_release_signoff"])
    release_notes_text = _read_text(paths["release_notes"])

    freeze_green_ok, freeze_green_data = _freeze_checklist_green(rc_freeze_text)
    checks.append(
        _make_check(
            "rc_freeze_checklist_green",
            freeze_green_ok,
            detail="RC freeze checklist must show blockers resolved and release blocker none-open.",
            data=freeze_green_data,
        )
    )

    rc_ready_ok, rc_ready_data = _rc_ready_for_signoff(rc_release_text)
    checks.append(
        _make_check(
            "rc_status_ready_for_signoff",
            rc_ready_ok,
            detail="RC release package must indicate READY-for-signoff and must not contain HOLD.",
            data=rc_ready_data,
        )
    )

    perf_ok, perf_data = _perf_artifacts_present(root)
    checks.append(
        _make_check(
            "performance_artifacts_present",
            perf_ok,
            detail="PERFORMANCE_RESULTS.json or live c1/c2/c5 artifacts must exist.",
            data=perf_data,
        )
    )

    frontend_doc_ok, frontend_doc_data = _frontend_build_requirement_documented(signoff_doc_text)
    checks.append(
        _make_check(
            "frontend_build_requirement_documented",
            frontend_doc_ok,
            detail="Signoff doc must include D:\\agent\\frontend and npm run build.",
            data=frontend_doc_data,
        )
    )

    safety_ok, safety_data = _production_safety_documented(signoff_doc_text)
    checks.append(
        _make_check(
            "production_safety_checklist_documented",
            safety_ok,
            detail="Signoff doc must include AUTH_ALLOW_DEV_BYPASS=false and production perf-bypass prohibition.",
            data=safety_data,
        )
    )

    notes_ok, notes_data = _release_notes_summary_complete(release_notes_text)
    checks.append(
        _make_check(
            "release_notes_phase_summary_complete",
            notes_ok,
            detail="Release notes must summarize Phase 123B, Phase 124, and known warnings.",
            data=notes_data,
        )
    )

    if not mock:
        status, error_text = _probe_health(base_url)
        checks.append(
            _make_check(
                "live_health_reachable",
                status == 200,
                blocked=status is None,
                detail=error_text or f"status={status}",
                data={"base_url": base_url, "status_code": status},
            )
        )

    failed = [check for check in checks if not check.passed and not check.blocked]
    blocked = [check for check in checks if check.blocked]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "mock" if mock else "live",
        "base_url": "not used" if mock else base_url,
        "ok": len(failed) == 0,
        "passed_count": sum(1 for check in checks if check.passed),
        "failed_count": len(failed),
        "blocked_count": len(blocked),
        "checks": [check.to_dict() for check in checks],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# TAOS Release Sign-off",
        "",
        f"- Generated: {report.get('generated_at')}",
        f"- Mode: `{report.get('mode')}`",
        f"- Base URL: `{report.get('base_url')}`",
        f"- Passed: **{report.get('passed_count')}**",
        f"- Failed: **{report.get('failed_count')}**",
        f"- Blocked: **{report.get('blocked_count')}**",
        "",
        "| Check | Status | Passed | Blocked | Detail |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for check in list(report.get("checks") or []):
        detail = str(check.get("detail") or "-").replace("|", "\\|")
        lines.append(
            f"| `{check.get('name')}` | {check.get('status')} | "
            f"{'yes' if check.get('passed') else 'no'} | "
            f"{'yes' if check.get('blocked') else 'no'} | {detail} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TAOS Phase 124 release sign-off checks.")
    parser.add_argument("--mock", action="store_true", help="Run deterministic local-safe sign-off checks.")
    parser.add_argument("--live", action="store_true", help="Run live sign-off checks (includes /health reachability).")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Live API base URL for /health verification.")
    parser.add_argument("--out-json", default="artifacts/release_outputs/QA_RESULTS_RELEASE_SIGNOFF.json")
    parser.add_argument("--out-md", default="artifacts/release_outputs/QA_RESULTS_RELEASE_SIGNOFF.md")
    args = parser.parse_args(argv)

    use_live = bool(args.live and not args.mock)
    report = build_report(mock=not use_live, base_url=args.base_url)
    out_json_path = Path(args.out_json)
    out_md_path = Path(args.out_md)
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    out_md_path.parent.mkdir(parents=True, exist_ok=True)
    out_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    out_md_path.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    return 0 if bool(report.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
