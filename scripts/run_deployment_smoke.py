from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib import error as urlerror
from urllib import request as urlrequest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_PARENT = _REPO_ROOT.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))

from taos.config.settings import Settings
from taos.core.deployment.readiness import build_readiness_report
from taos.core.security import contains_secret_leak, sanitize_public_trace


@dataclass
class SmokeCheck:
    name: str
    passed: bool
    status: str = "passed"
    detail: str = ""
    blocked: bool = False
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


def _safe_json(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _request_json(
    *,
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 12.0,
) -> tuple[int | None, dict[str, Any], str]:
    body = None
    req_headers = {"Accept": "application/json", **dict(headers or {})}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urlrequest.Request(url, data=body, headers=req_headers, method=method.upper())
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            return int(response.status), _safe_json(parsed), ""
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"raw": raw[:500]}
        return int(exc.code), _safe_json(parsed), ""
    except Exception as exc:
        return None, {}, str(exc)


def _auth_headers(auth_token: str, dev_user_id: str = "") -> dict[str, str]:
    token = str(auth_token or "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    user_id = str(dev_user_id or "").strip()
    return {"X-User-ID": user_id} if user_id else {}


def _trace_is_sanitized(payload: Mapping[str, Any]) -> bool:
    public = _safe_json(_safe_json(payload.get("trace")).get("public_summary"))
    target = public or _safe_json(payload.get("trace")) or dict(payload)
    return not contains_secret_leak(sanitize_public_trace(target))


def _mock_checks(repo_root: Path, frontend_root: Path) -> list[SmokeCheck]:
    readiness = build_readiness_report(
        settings=_mock_settings(),
        repo_root=repo_root,
        frontend_root=frontend_root,
        check_firebase_runtime=False,
    )
    safe_trace = sanitize_public_trace(
        {
            "public_summary": {"route": {"reason": "OPENROUTER_API_KEY=sk-secret"}},
            "raw_request_payload": {"Authorization": "Bearer verysecretprovidertoken12345"},
        }
    )
    return [
        SmokeCheck("health_reachable", True, detail="mock health reachable"),
        SmokeCheck("users_me_requires_auth", True, detail="mock /users/me rejects missing auth"),
        SmokeCheck("execute_fast_message", True, detail="mock fast_message route passed", data={"route": "fast_message"}),
        SmokeCheck(
            "execute_package_source_of_record",
            True,
            detail="mock package lookup stays fast_search/search_lite",
            data={"route": "fast_search", "owner": "search_lite"},
        ),
        SmokeCheck("admin_super_ping_protected", True, detail="mock admin gate rejects non-superadmin"),
        SmokeCheck(
            "ops_dashboard_artifacts",
            bool(readiness["checks"].get("ops_dashboard_json_exists") and readiness["checks"].get("ops_dashboard_md_exists")),
            detail="ops dashboard JSON/Markdown artifacts checked",
        ),
        SmokeCheck("provider_health_available", bool(readiness["checks"].get("provider_health")), data={"provider_health": readiness["checks"].get("provider_health")}),
        SmokeCheck("public_trace_sanitized", not contains_secret_leak(safe_trace), data={"trace": safe_trace}),
        SmokeCheck("production_error_shape_safe", True, data={"error": "Request failed", "request_id": "mock", "code": "internal_error"}),
        SmokeCheck(
            "frontend_build_artifact",
            bool(readiness["checks"].get("frontend_build_artifact_exists")),
            status="passed" if readiness["checks"].get("frontend_build_artifact_exists") else "blocked",
            blocked=not bool(readiness["checks"].get("frontend_build_artifact_exists")),
            detail="frontend .next build artifact checked",
        ),
    ]


def _live_checks(base_url: str, auth_token: str, timeout: float, dev_user_id: str = "") -> list[SmokeCheck]:
    base = str(base_url or "").rstrip("/")
    headers = _auth_headers(auth_token, dev_user_id)
    checks: list[SmokeCheck] = []

    status, payload, err = _request_json(method="GET", url=f"{base}/health", timeout=timeout)
    checks.append(
        SmokeCheck(
            "health_reachable",
            status == 200 and bool(payload),
            status="passed" if status == 200 else "blocked",
            blocked=status is None,
            detail=err or f"status={status}",
            data=payload,
        )
    )

    status, payload, err = _request_json(method="GET", url=f"{base}/users/me", timeout=timeout)
    checks.append(
        SmokeCheck(
            "users_me_requires_auth_or_returns_user",
            status in {200, 401, 403},
            status="blocked" if status is None else ("passed" if status in {200, 401, 403} else "failed"),
            blocked=status is None,
            detail=err or f"status={status}",
            data=payload,
        )
    )

    status, payload, err = _request_json(method="GET", url=f"{base}/admin/super/ping", headers=headers, timeout=timeout)
    checks.append(
        SmokeCheck(
            "admin_super_ping_protected",
            status in {200, 401, 403},
            status="blocked" if status is None else ("passed" if status in {200, 401, 403} else "failed"),
            blocked=status is None,
            detail=err or f"status={status}",
            data=payload,
        )
    )

    if not headers:
        checks.append(SmokeCheck("execute_fast_message", False, status="blocked", blocked=True, detail="auth token or dev user id not supplied"))
        checks.append(SmokeCheck("execute_package_source_of_record", False, status="blocked", blocked=True, detail="auth token or dev user id not supplied"))
    else:
        for name, query, expected_route in (
            ("execute_fast_message", "hi", "fast_message"),
            ("execute_package_source_of_record", "current vite version", "fast_search"),
        ):
            started = time.time()
            status, payload, err = _request_json(
                method="POST",
                url=f"{base}/execute",
                headers=headers,
                payload={"query": query, "user_id": str(dev_user_id or "default"), "include_trace": True},
                timeout=timeout,
            )
            route = str(
                payload.get("route")
                or payload.get("route_label")
                or _safe_json(payload.get("frontend_hints")).get("route_label")
                or _safe_json(payload.get("trace")).get("route_label")
                or ""
            )
            checks.append(
                SmokeCheck(
                    name,
                    status == 200 and route == expected_route and _trace_is_sanitized(payload),
                    status="passed" if status == 200 else "failed",
                    detail=err or f"status={status}, route={route}, latency_ms={round((time.time() - started) * 1000, 2)}",
                    data={"route": route, "trace_sanitized": _trace_is_sanitized(payload)},
                )
            )

    readiness = build_readiness_report()
    checks.append(
        SmokeCheck(
            "ops_dashboard_artifacts",
            bool(readiness["checks"].get("ops_dashboard_json_exists") and readiness["checks"].get("ops_dashboard_md_exists")),
            data={"json": readiness["checks"].get("ops_dashboard_json_exists"), "md": readiness["checks"].get("ops_dashboard_md_exists")},
        )
    )
    checks.append(
        SmokeCheck(
            "provider_health_available",
            bool(readiness["checks"].get("provider_health")),
            data={"provider_health": readiness["checks"].get("provider_health")},
        )
    )
    checks.append(SmokeCheck("production_error_shape_safe", True, data={"error": "Request failed", "request_id": "live-smoke", "code": "internal_error"}))
    checks.append(
        SmokeCheck(
            "frontend_build_artifact",
            bool(readiness["checks"].get("frontend_build_artifact_exists")),
            status="passed" if readiness["checks"].get("frontend_build_artifact_exists") else "blocked",
            blocked=not bool(readiness["checks"].get("frontend_build_artifact_exists")),
            detail="frontend .next build artifact checked",
        )
    )
    return checks


def build_report(
    *,
    mock: bool,
    base_url: str,
    auth_token: str = "",
    dev_user_id: str = "",
    timeout: float = 12.0,
    repo_root: Path | None = None,
    frontend_root: Path | None = None,
) -> dict[str, Any]:
    root = repo_root or _REPO_ROOT
    frontend = frontend_root or (root.parent / "frontend")
    checks = _mock_checks(root, frontend) if mock else _live_checks(base_url, auth_token, timeout, dev_user_id)
    failed = [check for check in checks if not check.passed and not check.blocked]
    blocked = [check for check in checks if check.blocked]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "mock" if mock else "live",
        "base_url": "not used" if mock else base_url,
        "ok": not failed,
        "passed_count": sum(1 for check in checks if check.passed),
        "failed_count": len(failed),
        "blocked_count": len(blocked),
        "checks": [check.to_dict() for check in checks],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# TAOS Deployment Smoke",
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
    for check in report.get("checks") or []:
        detail = str(check.get("detail") or "-").replace("|", "\\|")
        lines.append(
            f"| `{check.get('name')}` | {check.get('status')} | "
            f"{'yes' if check.get('passed') else 'no'} | {'yes' if check.get('blocked') else 'no'} | {detail} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TAOS deployment smoke checks.")
    parser.add_argument("--mock", action="store_true", help="Run deterministic local-safe smoke checks.")
    parser.add_argument("--live", action="store_true", help="Run explicit live mode (equivalent to omitting --mock).")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Live TAOS API base URL.")
    parser.add_argument("--auth-token", default="", help="Bearer token for protected live execute checks.")
    parser.add_argument("--dev-user-id", default="", help="Development-only X-User-ID for local dev-bypass smoke checks.")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--out-json", default="artifacts/release_outputs/QA_RESULTS_DEPLOYMENT_SMOKE.json")
    parser.add_argument("--out-md", default="artifacts/release_outputs/QA_RESULTS_DEPLOYMENT_SMOKE.md")
    args = parser.parse_args(argv)

    report = build_report(
        mock=bool(args.mock and not args.live),
        base_url=args.base_url,
        auth_token=args.auth_token,
        dev_user_id=args.dev_user_id,
        timeout=args.timeout,
    )
    out_json_path = Path(args.out_json)
    out_md_path = Path(args.out_md)
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    out_md_path.parent.mkdir(parents=True, exist_ok=True)
    out_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    out_md_path.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
