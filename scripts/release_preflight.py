from __future__ import annotations

import argparse
import json
import shutil
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

from taos.config.settings import Settings
from taos.core.deployment.readiness import build_readiness_report


@dataclass
class PreflightCheck:
    name: str
    passed: bool
    status: str = "passed"
    blocked: bool = False
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mock_settings() -> Settings:
    return Settings(
        _env_file=None,
        TAOS_ENV="production",
        DEBUG=False,
        OPENROUTER_API_KEY="mock-openrouter-key",
        SERPER_API_KEY="mock-serper-key",
        STORAGE_BACKEND="memory",
        ALLOW_MEMORY_FALLBACK_IN_PRODUCTION=True,
        AUTH_ALLOW_DEV_BYPASS=False,
        MAX_REQUEST_TIME_SECONDS=45,
        MAX_STEP_TIME=30,
        MAX_STEPS=15,
        MAX_UPLOAD_SIZE_MB=20,
    )


def _required_paths(root: Path) -> dict[str, Path]:
    return {
        "runbook_doc": root / "docs" / "RUNBOOK.md",
        "release_checklist_doc": root / "docs" / "RELEASE_CHECKLIST.md",
        "rollback_doc": root / "docs" / "ROLLBACK_PLAN.md",
        "incident_doc": root / "docs" / "INCIDENT_RESPONSE.md",
        "known_warnings_doc": root / "docs" / "KNOWN_WARNINGS.md",
        "smoke_script": root / "scripts" / "run_deployment_smoke.py",
        "full_qa_script": root / "scripts" / "run_full_live_qa_matrix.py",
        "document_qa_script": root / "scripts" / "run_document_live_qa.py",
        "ops_dashboard_script": root / "scripts" / "build_ops_dashboard.py",
        "research_eval_script": root / "scripts" / "run_research_eval.py",
    }


def _make_check(name: str, passed: bool, detail: str = "", blocked: bool = False, data: Mapping[str, Any] | None = None) -> PreflightCheck:
    if passed:
        status = "passed"
    elif blocked:
        status = "blocked"
    else:
        status = "failed"
    return PreflightCheck(
        name=name,
        passed=bool(passed),
        status=status,
        blocked=bool(blocked),
        detail=detail,
        data=dict(data or {}),
    )


def build_report(
    *,
    mock: bool,
    repo_root: Path | None = None,
    frontend_root: Path | None = None,
    check_firebase_runtime: bool | None = None,
) -> dict[str, Any]:
    root = repo_root or _REPO_ROOT
    frontend = frontend_root or (root.parent / "frontend")
    settings = _mock_settings() if mock else None
    firebase_runtime = False if mock and check_firebase_runtime is None else bool(check_firebase_runtime) if check_firebase_runtime is not None else True
    readiness = build_readiness_report(
        settings=settings,
        repo_root=root,
        frontend_root=frontend,
        check_firebase_runtime=firebase_runtime,
    )
    checks: list[PreflightCheck] = []

    checks.append(
        _make_check(
            "environment_ready",
            bool(readiness.get("checks", {}).get("env_ok")),
            detail=f"readiness_status={readiness.get('status')}",
            data={"errors": list(readiness.get("errors") or []), "warnings": list(readiness.get("warnings") or [])},
        )
    )
    checks.append(
        _make_check(
            "health_contract_available",
            bool(readiness.get("checks", {}).get("health_endpoint_available")),
        )
    )
    checks.append(
        _make_check(
            "provider_health_rows_available",
            bool(readiness.get("checks", {}).get("provider_health")),
        )
    )

    for label, path in _required_paths(root).items():
        checks.append(_make_check(f"{label}_exists", path.exists(), detail=str(path)))

    checks.append(
        _make_check(
            "frontend_root_exists",
            bool(readiness.get("checks", {}).get("frontend_root_exists")),
            blocked=not bool(readiness.get("checks", {}).get("frontend_root_exists")),
            detail=str(frontend),
        )
    )
    checks.append(
        _make_check(
            "frontend_build_artifact_exists",
            bool(readiness.get("checks", {}).get("frontend_build_artifact_exists")),
            blocked=not bool(readiness.get("checks", {}).get("frontend_build_artifact_exists")),
            detail="Expect .next/BUILD_ID or .next/server in D:\\agent\\frontend",
        )
    )
    checks.append(
        _make_check(
            "python_cli_available",
            bool(shutil.which("python")),
            blocked=not bool(shutil.which("python")),
            detail="Use py -3.13 if plain python is unavailable.",
        )
    )
    checks.append(
        _make_check(
            "python_runtime_supported",
            sys.version_info >= (3, 11),
            detail=f"runtime={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
    )

    failed = [c for c in checks if not c.passed and not c.blocked]
    blocked = [c for c in checks if c.blocked]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "mock" if mock else "live",
        "ok": len(failed) == 0,
        "passed_count": sum(1 for c in checks if c.passed),
        "failed_count": len(failed),
        "blocked_count": len(blocked),
        "required_env": [
            "TAOS_ENV",
            "OPENROUTER_API_KEY",
            "SERPER_API_KEY",
            "STORAGE_BACKEND",
            "FIREBASE_PROJECT_ID",
            "FIREBASE_CLIENT_EMAIL",
            "FIREBASE_PRIVATE_KEY",
        ],
        "readiness": readiness,
        "checks": [c.to_dict() for c in checks],
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


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# TAOS Release Preflight",
        "",
        f"- Generated: {report.get('generated_at')}",
        f"- Mode: `{report.get('mode')}`",
        f"- Passed: **{report.get('passed_count')}**",
        f"- Failed: **{report.get('failed_count')}**",
        f"- Blocked: **{report.get('blocked_count')}**",
        "",
        "## Required Environment Variables",
    ]
    for item in list(report.get("required_env") or []):
        lines.append(f"- `{item}`")
    lines.extend(
        [
            "",
            "| Check | Status | Passed | Blocked | Detail |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for check in list(report.get("checks") or []):
        detail = str(check.get("detail") or "-").replace("|", "\\|")
        lines.append(
            f"| `{check.get('name')}` | {check.get('status')} | "
            f"{'yes' if check.get('passed') else 'no'} | "
            f"{'yes' if check.get('blocked') else 'no'} | {detail} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TAOS release preflight checks.")
    parser.add_argument("--mock", action="store_true", help="Run deterministic local-safe preflight checks.")
    parser.add_argument("--live", action="store_true", help="Run preflight checks against active environment settings.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Live API base URL for health reachability check.")
    parser.add_argument("--out-json", default="QA_RESULTS_RELEASE_PREFLIGHT.json")
    parser.add_argument("--out-md", default="QA_RESULTS_RELEASE_PREFLIGHT.md")
    args = parser.parse_args(argv)

    use_live = bool(args.live and not args.mock)
    report = build_report(mock=not use_live)
    if use_live:
        status, error_text = _probe_health(args.base_url)
        live_check = _make_check(
            "live_health_reachable",
            bool(status == 200),
            blocked=status is None,
            detail=error_text or f"status={status}",
            data={"base_url": args.base_url, "status_code": status},
        )
        checks = list(report.get("checks") or [])
        checks.append(live_check.to_dict())
        failed = [c for c in checks if not c.get("passed") and not c.get("blocked")]
        blocked = [c for c in checks if c.get("blocked")]
        report["checks"] = checks
        report["passed_count"] = sum(1 for c in checks if c.get("passed"))
        report["failed_count"] = len(failed)
        report["blocked_count"] = len(blocked)
        report["ok"] = len(failed) == 0
        report["base_url"] = args.base_url
    Path(args.out_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    Path(args.out_md).write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    return 0 if bool(report.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
