from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Request

from taos.apps.api.auth_context import require_superadmin
from taos.apps.api.middleware.error_handler import ErrorHandlerMiddleware
from taos.apps.api.routes.health import health_check
from taos.config.settings import Settings
from taos.core.deployment.env_validator import validate_production_environment
from taos.core.deployment.readiness import build_readiness_report
from taos.core.security import contains_secret_leak
from taos.scripts import run_deployment_smoke


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "TAOS_ENV": "production",
        "DEBUG": False,
        "OPENROUTER_API_KEY": "test-openrouter",
        "SERPER_API_KEY": "test-serper",
        "STORAGE_BACKEND": "memory",
        "ALLOW_MEMORY_FALLBACK_IN_PRODUCTION": True,
        "AUTH_ALLOW_DEV_BYPASS": False,
        "MAX_REQUEST_TIME_SECONDS": 45,
        "MAX_STEP_TIME": 30,
        "MAX_STEPS": 15,
        "MAX_UPLOAD_SIZE_MB": 20,
    }
    base.update(overrides)
    return Settings(**base)


def _sandbox_roots() -> tuple[Path, Path]:
    root = Path("tmp_phase119_test_artifacts") / uuid4().hex
    frontend = root / "frontend"
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (frontend / ".next").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "ops_dashboard_latest.json").write_text("{}", encoding="utf-8")
    (root / "docs" / "ops_dashboard_latest.md").write_text("# ops\n", encoding="utf-8")
    (frontend / ".next" / "BUILD_ID").write_text("phase119", encoding="utf-8")
    return root, frontend


def _request_with_claims(claims: dict) -> Request:
    scope = {
        "type": "http",
        "headers": [(b"x-request-id", b"phase119")],
        "method": "GET",
        "path": "/admin/super/ping",
        "query_string": b"",
    }
    request = Request(scope)
    request.state.auth_uid = claims.get("uid", "u1")
    request.state.auth_claims = claims
    request.state.auth_user = {"uid": claims.get("uid", "u1"), "claims": claims}
    return request


def test_production_env_validator_reports_missing_critical_vars() -> None:
    report = validate_production_environment(
        _settings(
            OPENROUTER_API_KEY="",
            STORAGE_BACKEND="firebase",
            FIREBASE_PROJECT_ID="",
            FIREBASE_CLIENT_EMAIL="",
            FIREBASE_PRIVATE_KEY="",
            AUTH_ALLOW_DEV_BYPASS=True,
            DEBUG=True,
        ),
        environ={"CORS_ALLOW_ORIGINS": "*"},
    )

    assert report["ok"] is False
    assert any("OPENROUTER_API_KEY" in item for item in report["errors"])
    assert any("FIREBASE_" in item for item in report["errors"])
    assert any("AUTH_ALLOW_DEV_BYPASS" in item for item in report["errors"])
    assert any("DEBUG" in item for item in report["errors"])
    assert any("CORS_ALLOW_ORIGINS" in item for item in report["errors"])


def test_readiness_report_validates_artifacts_and_provider_rows() -> None:
    root, frontend = _sandbox_roots()
    report = build_readiness_report(
        settings=_settings(),
        repo_root=root,
        frontend_root=frontend,
        check_firebase_runtime=False,
    )

    assert report["ready"] is True
    assert report["checks"]["ops_dashboard_json_exists"] is True
    assert report["checks"]["frontend_build_artifact_exists"] is True
    assert "openrouter" in report["checks"]["provider_health"]
    assert "serper" in report["checks"]["provider_health"]


@pytest.mark.asyncio
async def test_health_endpoint_exposes_readiness_contract() -> None:
    response = await health_check()

    assert response.status in {"healthy", "degraded"}
    assert isinstance(response.ready, bool)
    assert "provider_health" in response.checks
    assert "ops_dashboard_json_exists" in response.checks


def test_deployment_smoke_mock_mode_passes_and_writes_safe_report() -> None:
    root, frontend = _sandbox_roots()
    report = run_deployment_smoke.build_report(
        mock=True,
        base_url="http://example.test",
        repo_root=root,
        frontend_root=frontend,
    )

    assert report["ok"] is True
    assert report["failed_count"] == 0
    assert any(check["name"] == "execute_fast_message" for check in report["checks"])
    assert any(check["name"] == "execute_package_source_of_record" for check in report["checks"])
    assert not contains_secret_leak(report)


def test_deployment_smoke_live_mode_detects_api_blocked_state(monkeypatch) -> None:
    def fake_request_json(**_kwargs):
        return None, {}, "connection refused"

    monkeypatch.setattr(run_deployment_smoke, "_request_json", fake_request_json)
    report = run_deployment_smoke.build_report(mock=False, base_url="http://localhost:8000")

    assert report["ok"] is True
    assert report["blocked_count"] >= 1
    assert any(check["name"] == "health_reachable" and check["blocked"] for check in report["checks"])


@pytest.mark.asyncio
async def test_production_error_shape_remains_safe() -> None:
    request = _request_with_claims({"uid": "u1", "role": "user"})
    middleware = ErrorHandlerMiddleware(app=lambda _scope, _receive, _send: None)
    middleware._settings = SimpleNamespace(is_development=False)

    async def failing_call_next(_request):
        raise RuntimeError("Traceback OPENROUTER_API_KEY=sk-secret D:\\agent\\taos\\.env")

    response = await middleware.dispatch(request, failing_call_next)
    body = json.loads(response.body.decode("utf-8"))

    assert body["error"] == "Request failed"
    assert body["code"] == "internal_error"
    assert not contains_secret_leak(body)


def test_admin_superadmin_protection_remains_intact() -> None:
    with pytest.raises(Exception):
        require_superadmin(_request_with_claims({"uid": "u1", "role": "user"}))


def test_smoke_cli_supports_base_url_and_mock_mode() -> None:
    source = Path("scripts/run_deployment_smoke.py").read_text(encoding="utf-8")

    assert "--base-url" in source
    assert "--mock" in source
