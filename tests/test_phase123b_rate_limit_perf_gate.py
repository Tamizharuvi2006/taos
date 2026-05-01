from __future__ import annotations

from types import SimpleNamespace

from taos.apps.api.errors import build_error
from taos.core.limits import quota_manager as quota_mod
from taos.core.limits.quota_manager import QuotaManager
from taos.scripts import run_performance_load_test as perf


def _ok_preflight(*, base_url: str, timeout: float, headers, auth_mode: str):
    return {
        "auth_mode": auth_mode,
        "ok": True,
        "failures": [],
        "health": {"status_code": 200, "latency_ms": 1.0, "error": "", "request_id": "", "response_snippet": ""},
        "users_me": {"status_code": 200, "latency_ms": 1.0, "error": "", "request_id": "", "response_snippet": ""},
    }


def test_429_response_includes_safe_error_code_and_request_id() -> None:
    body = build_error(
        code="RATE_LIMITED",
        message="Rate limit exceeded",
        request_id="req_123",
        route="fast_search",
        owner="search_lite",
        retry_after_seconds=30,
    )

    assert body["error_code"] == "RATE_LIMITED"
    assert body["code"] == "rate_limited"
    assert body["request_id"] == "req_123"
    assert body["route"] == "fast_search"
    assert body["owner"] == "search_lite"
    assert body["retry_after_seconds"] == 30


def test_429_with_known_route_does_not_become_unknown_route_failure(monkeypatch) -> None:
    monkeypatch.setattr(perf, "_run_live_preflight", _ok_preflight)

    def fake_http_json_request(*, method, url, headers=None, payload=None, timeout=20.0):
        body = {
            "error_code": "RATE_LIMITED",
            "code": "rate_limited",
            "message": "Rate limit exceeded (15/minute)",
            "request_id": "rl_1",
            "route": "fast_search",
            "owner": "search_lite",
            "retry_after_seconds": 30,
        }
        return 429, body, 7.0, "", "", {"x-request-id": "rl_1"}

    monkeypatch.setattr(perf, "_http_json_request", fake_http_json_request)

    case = perf.PerformanceCase(id="c1", query="current vite version", expected_route="fast_search", request_count=1)
    report = perf.run_load_test(
        cases=[case],
        live=True,
        explicit_live=True,
        dev_user_id="perf_dev",
        warmup_requests=0,
        concurrency_override=1,
    )

    assert report["reliability"]["rate_limited_failures"] == 1
    assert report["reliability"]["unknown_route_failures"] == 0
    assert report["reliability"]["contract_failures"] == 0
    assert report["failures"][0]["route"] == "fast_search"


def test_runner_classifies_rate_limited_separately_from_unknown(monkeypatch) -> None:
    monkeypatch.setattr(perf, "_run_live_preflight", _ok_preflight)

    def fake_http_json_request(*, method, url, headers=None, payload=None, timeout=20.0):
        body = {
            "error_code": "RATE_LIMITED",
            "code": "rate_limited",
            "message": "Rate limit exceeded (15/minute)",
            "request_id": "rl_2",
        }
        return 429, body, 9.0, "", "", {"x-request-id": "rl_2"}

    monkeypatch.setattr(perf, "_http_json_request", fake_http_json_request)

    case = perf.PerformanceCase(id="c2", query="current vite version", expected_route="fast_search", request_count=1)
    report = perf.run_load_test(
        cases=[case],
        live=True,
        explicit_live=True,
        dev_user_id="perf_dev",
        warmup_requests=0,
        concurrency_override=1,
    )

    assert report["reliability"]["rate_limited_failures"] == 1
    assert report["reliability"]["unknown_route_failures"] == 0
    assert report["reliability"]["contract_failures"] == 1
    checks = report["reliability"]["failed_check_counts"]
    assert checks.get("rate_limited") == 1
    assert checks.get("contract_failure") == 1


def test_perf_mode_raises_quota_only_in_development(monkeypatch) -> None:
    monkeypatch.setattr(
        quota_mod,
        "get_settings",
        lambda: SimpleNamespace(
            is_development=True,
            auth_allow_dev_bypass=True,
            perf_test_mode=False,
            perf_test_user_id="perf_user",
            perf_test_per_minute_limit=120,
        ),
    )
    manager = QuotaManager()
    last = None
    for _ in range(20):
        last = manager.check_and_consume_detailed(
            user_id="perf_user",
            tier="free",
            perf_test_mode_requested=True,
        )

    assert last is not None
    assert last.allowed is True
    assert last.perf_mode_applied is True
    assert last.limit_per_minute == 120


def test_perf_mode_is_rejected_in_production(monkeypatch) -> None:
    monkeypatch.setattr(
        quota_mod,
        "get_settings",
        lambda: SimpleNamespace(
            is_development=False,
            auth_allow_dev_bypass=True,
            perf_test_mode=True,
            perf_test_user_id="perf_user",
            perf_test_per_minute_limit=120,
        ),
    )
    manager = QuotaManager()
    final = None
    for _ in range(16):
        final = manager.check_and_consume_detailed(
            user_id="perf_user",
            tier="free",
            perf_test_mode_requested=True,
        )

    assert final is not None
    assert final.allowed is False
    assert final.perf_mode_applied is False
    assert final.limit_per_minute == 15


def test_normal_users_keep_normal_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        quota_mod,
        "get_settings",
        lambda: SimpleNamespace(
            is_development=True,
            auth_allow_dev_bypass=True,
            perf_test_mode=True,
            perf_test_user_id="perf_user",
            perf_test_per_minute_limit=120,
        ),
    )
    manager = QuotaManager()
    final = None
    for _ in range(16):
        final = manager.check_and_consume_detailed(
            user_id="regular_dev_user",
            tier="free",
            perf_test_mode_requested=True,
        )

    assert final is not None
    assert final.allowed is False
    assert final.perf_mode_applied is False
    assert final.limit_per_minute == 15


def test_reliability_gate_passes_with_clean_live_responses(monkeypatch) -> None:
    monkeypatch.setattr(perf, "_run_live_preflight", _ok_preflight)

    def fake_http_json_request(*, method, url, headers=None, payload=None, timeout=20.0):
        body = {"route": "fast_message", "metadata": {"route_owner": "direct"}, "request_id": "ok_1"}
        return 200, body, 6.0, "", "", {"x-request-id": "ok_1"}

    monkeypatch.setattr(perf, "_http_json_request", fake_http_json_request)

    case = perf.PerformanceCase(id="c3", query="hi", expected_route="fast_message", request_count=2, max_p95_ms=1000)
    report = perf.run_load_test(
        cases=[case],
        live=True,
        explicit_live=True,
        dev_user_id="perf_dev",
        warmup_requests=0,
        concurrency_override=1,
    )

    assert report["reliability"]["passed"] is True
    assert report["latency_gate"]["passed"] is True
    assert report["ok"] is True


def test_reliability_gate_fails_on_rate_limited_unless_explicit_override(monkeypatch) -> None:
    monkeypatch.setattr(perf, "_run_live_preflight", _ok_preflight)

    def fake_http_json_request(*, method, url, headers=None, payload=None, timeout=20.0):
        body = {
            "error_code": "RATE_LIMITED",
            "code": "rate_limited",
            "message": "Rate limit exceeded (15/minute)",
            "request_id": "rl_3",
            "route": "fast_search",
            "owner": "search_lite",
        }
        return 429, body, 8.0, "", "", {"x-request-id": "rl_3"}

    monkeypatch.setattr(perf, "_http_json_request", fake_http_json_request)

    case = perf.PerformanceCase(id="c4", query="current vite version", expected_route="fast_search", request_count=1)
    strict_report = perf.run_load_test(
        cases=[case],
        live=True,
        explicit_live=True,
        dev_user_id="perf_dev",
        warmup_requests=0,
        concurrency_override=1,
    )
    assert strict_report["reliability"]["passed"] is False

    override_report = perf.run_load_test(
        cases=[case],
        live=True,
        explicit_live=True,
        dev_user_id="perf_dev",
        warmup_requests=0,
        concurrency_override=1,
        allow_rate_limited_failures=True,
        reliability_error_rate_budget=1.0,
    )
    assert override_report["reliability"]["passed"] is True
