from __future__ import annotations

from pathlib import Path

import pytest

from taos.apps.api.errors import build_error
from taos.apps.api.routes.health import health_check
from taos.core.monitoring.ops_dashboard import build_ops_dashboard
from taos.core.routing import RouteDecider
from taos.orchestration.route_dispatcher import RouteDispatcher


@pytest.mark.asyncio
async def test_health_contract_valid() -> None:
    response = await health_check()

    assert response.status in {"healthy", "degraded"}
    assert isinstance(response.ready, bool)
    assert isinstance(response.checks, dict)
    assert response.uptime_seconds >= 0


def test_ops_dashboard_artifact_exists() -> None:
    assert Path("docs/ops_dashboard_latest.json").exists()
    assert Path("docs/ops_dashboard_latest.md").exists()


def test_provider_health_summary_safe_when_missing() -> None:
    dashboard = build_ops_dashboard(execution_records=[{"route": "fast_message", "latency_ms": 100}])

    assert dashboard["provider_health"]["openrouter"]["state"] == "unknown"
    assert dashboard["provider_health"]["serper"]["failures"] == 0


def test_production_error_shape_hides_raw_stack_trace() -> None:
    body = build_error(code="INTERNAL_SERVER_ERROR", message="Internal server error", request_id="r1")

    assert "traceback" not in body
    assert "Traceback" not in str(body)
    assert body["message"] == "Internal server error"


def test_fast_package_lookup_stays_search_lite_and_not_firebase_path() -> None:
    decision = RouteDecider().decide_sync_for_tests("current vite version")

    assert decision.route == "fast_search"
    assert RouteDispatcher().owner_for_route(decision.route) == "search_lite"
    assert RouteDispatcher().owner_for_route(decision.route) != "document_pipeline"
