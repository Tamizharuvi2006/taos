from __future__ import annotations

import json

from taos.scripts import run_performance_load_test as perf


def _ok_preflight(*, base_url: str, timeout: float, headers, auth_mode: str):
    return {
        "auth_mode": auth_mode,
        "ok": True,
        "failures": [],
        "health": {"status_code": 200, "latency_ms": 1.0, "error": "", "request_id": "", "response_snippet": ""},
        "users_me": {"status_code": 200, "latency_ms": 1.0, "error": "", "request_id": "", "response_snippet": ""},
    }


def test_failed_non_200_response_records_status_and_snippet(monkeypatch) -> None:
    monkeypatch.setattr(perf, "_run_live_preflight", _ok_preflight)

    def fake_http_json_request(*, method, url, headers=None, payload=None, timeout=20.0):
        body = {"detail": {"code": "UNAUTHORIZED", "message": "bad token", "request_id": "req-401"}}
        return 401, body, 12.0, "", json.dumps(body), {"x-request-id": "hdr-401"}

    monkeypatch.setattr(perf, "_http_json_request", fake_http_json_request)

    case = perf.PerformanceCase(id="c1", query="hi", expected_route="fast_message", request_count=1)
    report = perf.run_load_test(
        cases=[case],
        live=True,
        explicit_live=True,
        dev_user_id="dev-qa",
        warmup_requests=0,
        concurrency_override=1,
    )

    assert report["failed"] == 1
    failure = report["failures"][0]
    assert failure["status_code"] == 401
    assert "bad token" in failure["response_snippet"]
    assert "http_status" in failure["failed_checks"]


def test_route_missing_is_classified_as_reliability_failure(monkeypatch) -> None:
    monkeypatch.setattr(perf, "_run_live_preflight", _ok_preflight)

    def fake_http_json_request(*, method, url, headers=None, payload=None, timeout=20.0):
        return 200, {"answer": "ok"}, 8.0, "", '{"answer":"ok"}', {}

    monkeypatch.setattr(perf, "_http_json_request", fake_http_json_request)

    case = perf.PerformanceCase(id="c2", query="hi", expected_route="fast_message", request_count=1)
    report = perf.run_load_test(
        cases=[case],
        live=True,
        explicit_live=True,
        dev_user_id="dev-qa",
        warmup_requests=0,
        concurrency_override=1,
    )

    assert report["reliability"]["passed"] is False
    assert report["reliability"]["unknown_route_failures"] == 1
    assert "route_missing" in report["reliability"]["failed_check_counts"]


def test_latency_success_does_not_hide_reliability_failure(monkeypatch) -> None:
    monkeypatch.setattr(perf, "_run_live_preflight", _ok_preflight)

    def fake_http_json_request(*, method, url, headers=None, payload=None, timeout=20.0):
        return 200, {"route": "unknown"}, 5.0, "", '{"route":"unknown"}', {}

    monkeypatch.setattr(perf, "_http_json_request", fake_http_json_request)

    case = perf.PerformanceCase(id="c3", query="hi", expected_route="fast_message", max_p95_ms=1000, request_count=2)
    report = perf.run_load_test(
        cases=[case],
        live=True,
        explicit_live=True,
        dev_user_id="dev-qa",
        warmup_requests=0,
        concurrency_override=1,
    )

    assert report["latency_gate"]["passed"] is True
    assert report["reliability"]["passed"] is False
    assert report["ok"] is False


def test_warmup_requests_are_excluded_from_metrics() -> None:
    case = perf.PerformanceCase(id="c4", query="hi", expected_route="fast_message", request_count=4)
    report = perf.run_load_test(
        cases=[case],
        live=False,
        warmup_requests=3,
        concurrency_override=1,
    )

    assert report["total_requests"] == 4
    assert report["warmup_executed_total"] == 3


def test_dev_user_id_and_auth_mode_are_passed_to_live_request_builder(monkeypatch) -> None:
    monkeypatch.setattr(perf, "_run_live_preflight", _ok_preflight)
    captured: dict[str, object] = {}

    def fake_build_live_request_row(
        *,
        case,
        sample_index,
        cache_profile,
        base_url,
        timeout,
        headers,
        user_id,
        auth_mode,
        concurrency,
    ):
        captured["headers"] = dict(headers)
        captured["user_id"] = user_id
        captured["auth_mode"] = auth_mode
        return {
            "case_id": case.id,
            "query": case.query,
            "expected_route": case.expected_route,
            "observed_route": case.expected_route,
            "owner": "direct",
            "status_code": 200,
            "latency_ms": 9.0,
            "fallback_used": False,
            "error": "",
            "error_code": "",
            "request_id": "",
            "response_snippet": "",
            "failed_checks": [],
            "is_error": False,
            "cache_profile": cache_profile,
            "concurrency_used": concurrency,
            "auth_mode": auth_mode,
            "sample_index": sample_index,
        }

    monkeypatch.setattr(perf, "_build_live_request_row", fake_build_live_request_row)

    case = perf.PerformanceCase(id="c5", query="hi", expected_route="fast_message", request_count=1)
    perf.run_load_test(
        cases=[case],
        live=True,
        explicit_live=True,
        auth_token="token-123",
        dev_user_id="dev-user-5",
        warmup_requests=0,
        concurrency_override=1,
    )

    headers = dict(captured["headers"])
    assert headers["Authorization"] == "Bearer token-123"
    assert headers["X-User-ID"] == "dev-user-5"
    assert captured["user_id"] == "dev-user-5"
    assert captured["auth_mode"] == "bearer"


def test_error_rate_calculation_is_correct(monkeypatch) -> None:
    monkeypatch.setattr(perf, "_run_live_preflight", _ok_preflight)

    def fake_build_live_request_row(
        *,
        case,
        sample_index,
        cache_profile,
        base_url,
        timeout,
        headers,
        user_id,
        auth_mode,
        concurrency,
    ):
        is_error = sample_index % 2 == 0
        failed_checks = ["http_status"] if is_error else []
        return {
            "case_id": case.id,
            "query": case.query,
            "expected_route": case.expected_route,
            "observed_route": case.expected_route,
            "owner": "direct",
            "status_code": 500 if is_error else 200,
            "latency_ms": 10.0,
            "fallback_used": False,
            "error": "",
            "error_code": "internal_error" if is_error else "",
            "request_id": "",
            "response_snippet": "boom" if is_error else "",
            "failed_checks": failed_checks,
            "is_error": is_error,
            "cache_profile": cache_profile,
            "concurrency_used": concurrency,
            "auth_mode": auth_mode,
            "sample_index": sample_index,
        }

    monkeypatch.setattr(perf, "_build_live_request_row", fake_build_live_request_row)

    case = perf.PerformanceCase(id="c6", query="hi", expected_route="fast_message", request_count=4)
    report = perf.run_load_test(
        cases=[case],
        live=True,
        explicit_live=True,
        dev_user_id="dev-qa",
        warmup_requests=0,
        concurrency_override=1,
    )

    assert report["error_rate"] == 0.5


def test_unknown_route_failures_are_reported_clearly(monkeypatch) -> None:
    monkeypatch.setattr(perf, "_run_live_preflight", _ok_preflight)

    def fake_http_json_request(*, method, url, headers=None, payload=None, timeout=20.0):
        return 200, {"route": "unknown"}, 6.0, "", '{"route":"unknown"}', {}

    monkeypatch.setattr(perf, "_http_json_request", fake_http_json_request)

    case = perf.PerformanceCase(id="c7", query="hi", expected_route="fast_message", request_count=2)
    report = perf.run_load_test(
        cases=[case],
        live=True,
        explicit_live=True,
        dev_user_id="dev-qa",
        warmup_requests=0,
        concurrency_override=1,
    )

    assert report["reliability"]["unknown_route_failures"] == 2
    assert report["reliability"]["failed_check_counts"].get("route_unknown") == 2
    assert all("route_unknown" in row["failed_checks"] for row in report["failures"])
