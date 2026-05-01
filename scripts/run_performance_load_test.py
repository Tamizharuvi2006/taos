from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib import error as urlerror
from urllib import request as urlrequest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_PARENT = _REPO_ROOT.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))

from taos.core.monitoring.metrics_collector import percentile
from taos.core.governance import QuotaManager, UsageMeter, default_cost_policy


@dataclass
class PerformanceCase:
    id: str
    query: str
    expected_route: str
    concurrency: int = 1
    max_p95_ms: int = 3000
    request_count: int = 10

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "PerformanceCase":
        return cls(
            id=str(row.get("id") or "").strip(),
            query=str(row.get("query") or "").strip(),
            expected_route=str(row.get("expected_route") or "").strip(),
            concurrency=max(1, int(row.get("concurrency") or 1)),
            max_p95_ms=max(1, int(row.get("max_p95_ms") or 3000)),
            request_count=max(1, int(row.get("request_count") or 10)),
        )


def load_cases(path: str | Path) -> list[PerformanceCase]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [PerformanceCase.from_dict(row) for row in rows if isinstance(row, Mapping)]


def calculate_percentiles(latencies: Iterable[float]) -> dict[str, float | None]:
    values = [float(x) for x in latencies if float(x) >= 0]
    return {
        "p50_ms": percentile(values, 50),
        "p90_ms": percentile(values, 90),
        "p95_ms": percentile(values, 95),
    }


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _truncate(value: str, limit: int = 280) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _normalize_base_url(base_url: str) -> str:
    value = str(base_url or "").strip()
    if not value:
        return "http://localhost:8000"
    lowered = value.lower()
    if lowered.startswith("http://localhost"):
        return "http://127.0.0.1" + value[len("http://localhost") :]
    if lowered.startswith("https://localhost"):
        return "https://127.0.0.1" + value[len("https://localhost") :]
    return value


def _route_owner(route: str) -> str:
    budget = default_cost_policy().budget_for(route)
    if budget.owner:
        return budget.owner
    if route in {"fast_search"}:
        return "search_lite"
    if route in {"official_search", "deep_search", "news_search", "comparison_search"}:
        return "research_pipeline"
    if route in {"no_search", "fast_message"}:
        return "direct"
    return "unknown"


def _route_budget(route: str) -> dict[str, Any]:
    return default_cost_policy().budget_for(route).__dict__.copy()


def _usage_from_payload(payload: Mapping[str, Any], route: str) -> dict[str, Any]:
    trace = _safe_dict(payload.get("trace"))
    metadata = _safe_dict(payload.get("metadata"))
    usage = _safe_dict(payload.get("usage") or trace.get("usage") or metadata.get("usage"))
    if usage:
        return usage
    meter = UsageMeter.from_trace(route, trace)
    return meter.to_dict()


def _quota_from_usage(usage: Mapping[str, Any]) -> dict[str, Any]:
    meter = UsageMeter(route=str(usage.get("route") or "task"))
    snapshot = meter.snapshot()
    snapshot.route = str(usage.get("route") or snapshot.route)
    snapshot.route_owner = str(usage.get("route_owner") or "")
    snapshot.llm_calls = int(usage.get("llm_calls") or 0)
    snapshot.llm_tokens = int(usage.get("llm_tokens") or 0)
    snapshot.search_calls = int(usage.get("search_calls") or 0)
    snapshot.extract_calls = int(usage.get("extract_calls") or 0)
    snapshot.package_registry_calls = int(usage.get("package_registry_calls") or 0)
    snapshot.cache_hits = int(usage.get("cache_hits") or 0)
    snapshot.fallback_count = int(usage.get("fallback_count") or 0)
    snapshot.estimated_cost_usd = float(usage.get("estimated_cost_usd") or 0.0)
    snapshot.budget_exceeded = bool(usage.get("budget_exceeded"))
    snapshot.latency_ms = float(usage.get("latency_ms") or 0.0)
    snapshot.web_search_allowed = bool(usage.get("web_search_allowed"))
    snapshot.research_allowed = bool(usage.get("research_allowed"))
    snapshot.doc_pipeline_allowed = bool(usage.get("doc_pipeline_allowed"))
    snapshot.fsm_allowed = bool(usage.get("fsm_allowed"))
    snapshot.entity_pipeline_called = bool(usage.get("entity_pipeline_called"))
    snapshot.doc_pipeline_called = bool(usage.get("doc_pipeline_called"))
    snapshot.fsm_called = bool(usage.get("fsm_called"))
    decision = QuotaManager().check(snapshot)
    return decision.to_dict()


def _mark_usage_budget_if_needed(usage: dict[str, Any], quota: Mapping[str, Any]) -> dict[str, Any]:
    updated = dict(usage or {})
    if not bool(_safe_dict(quota).get("allowed", True)):
        updated["budget_exceeded"] = True
    return updated


def _observed_route(payload: Mapping[str, Any], fallback: str = "") -> str:
    trace = _safe_dict(payload.get("trace"))
    metadata = _safe_dict(payload.get("metadata"))
    hints = _safe_dict(payload.get("frontend_hints"))
    route = str(
        payload.get("route")
        or payload.get("route_label")
        or hints.get("route_label")
        or trace.get("route_label")
        or metadata.get("route")
        or fallback
    ).strip()
    return route or fallback


def _fallback_used(payload: Mapping[str, Any]) -> bool:
    trace = _safe_dict(payload.get("trace"))
    metadata = _safe_dict(payload.get("metadata"))
    if bool(payload.get("fallback_used") or trace.get("fallback_used") or metadata.get("fallback_used")):
        return True
    provider_health = _safe_dict(payload.get("provider_health") or trace.get("provider_health") or metadata.get("provider_health"))
    for row in provider_health.values():
        if isinstance(row, Mapping) and bool(row.get("fallback_used")):
            return True
    return False


def _extract_request_id(payload: Mapping[str, Any], response_headers: Mapping[str, str]) -> str:
    detail = _safe_dict(payload.get("detail"))
    trace = _safe_dict(payload.get("trace"))
    candidates = [
        payload.get("request_id"),
        detail.get("request_id"),
        trace.get("request_id"),
        response_headers.get("x-request-id"),
        response_headers.get("request-id"),
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_error_code(payload: Mapping[str, Any], status_code: int | None, transport_error: str) -> str:
    detail = _safe_dict(payload.get("detail"))
    error_obj = _safe_dict(payload.get("error"))
    candidates = [
        payload.get("error_code"),
        payload.get("code"),
        detail.get("error_code"),
        detail.get("code"),
        error_obj.get("error_code"),
        error_obj.get("code"),
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    if transport_error:
        return "transport_error"
    if status_code in {401, 403}:
        return "auth_error"
    if status_code is not None and status_code >= 500:
        return "internal_error"
    if status_code is not None and status_code >= 400:
        return "http_error"
    return ""


def _is_rate_limited(*, status_code: int | None, error_code: str, response_snippet: str) -> bool:
    code = str(error_code or "").strip().lower()
    snippet = str(response_snippet or "").strip().lower()
    if status_code == 429:
        return True
    if code in {"rate_limited", "rate-limit", "too_many_requests", "too_many_requests_error"}:
        return True
    if "rate limit exceeded" in snippet or "too many requests" in snippet:
        return True
    return False


def _response_snippet(payload: Mapping[str, Any], raw_body: str, transport_error: str) -> str:
    if raw_body.strip():
        return _truncate(raw_body, limit=360)
    if payload:
        return _truncate(json.dumps(payload, ensure_ascii=True), limit=360)
    if transport_error:
        return _truncate(transport_error, limit=360)
    return ""


def _build_failed_checks(
    *,
    status_code: int | None,
    transport_error: str,
    observed_route: str,
    expected_route: str,
    error_code: str,
    request_id: str,
    response_snippet: str,
) -> list[str]:
    checks: list[str] = []
    code = str(error_code or "").lower()
    snippet = str(response_snippet or "").lower()
    is_rate_limited = _is_rate_limited(
        status_code=status_code,
        error_code=error_code,
        response_snippet=response_snippet,
    )

    if transport_error:
        checks.append("transport_error")
    if status_code is None:
        checks.append("status_missing")
    elif status_code >= 400:
        checks.append("http_status")
    if is_rate_limited:
        checks.append("rate_limited")
    if status_code in {401, 403} or code in {"auth_error", "unauthorized", "forbidden"}:
        checks.append("auth_failure")
    route = str(observed_route or "").strip()
    if not route:
        if is_rate_limited:
            checks.append("contract_failure")
        else:
            checks.append("route_missing")
    elif route == "unknown":
        if is_rate_limited:
            checks.append("contract_failure")
        else:
            checks.append("route_unknown")
    if status_code is not None and status_code >= 400:
        if not str(error_code or "").strip():
            checks.append("contract_failure")
        if not str(request_id or "").strip():
            checks.append("contract_failure")
    if status_code is not None and status_code >= 500:
        checks.append("internal_error")
    if "internal_error" in code or "traceback" in snippet:
        checks.append("internal_error")
    if status_code is not None and status_code < 400 and observed_route and expected_route and observed_route != expected_route:
        checks.append("route_mismatch")
    deduped: list[str] = []
    for check in checks:
        if check not in deduped:
            deduped.append(check)
    return deduped


def _auth_headers(auth_token: str, dev_user_id: str, *, perf_test_mode: bool = False) -> tuple[dict[str, str], str]:
    token = str(auth_token or "").strip()
    user_id = str(dev_user_id or "").strip()
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if user_id:
        headers["X-User-ID"] = user_id
    if bool(perf_test_mode):
        headers["X-Perf-Test-Mode"] = "true"
    if token:
        return headers, "bearer"
    if user_id:
        return headers, "dev_bypass"
    return headers, "none"


def _http_json_request(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    payload: Mapping[str, Any] | None = None,
    timeout: float = 20.0,
) -> tuple[int | None, dict[str, Any], float, str, str, dict[str, str]]:
    req_headers = {"Accept": "application/json", **dict(headers or {})}
    body: bytes | None = None
    if payload is not None:
        body = json.dumps(dict(payload)).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urlrequest.Request(url, data=body, headers=req_headers, method=method.upper())
    started = time.perf_counter()
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            headers_out = {str(k).lower(): str(v) for k, v in dict(response.headers or {}).items()}
            return int(response.status), _safe_dict(parsed), (time.perf_counter() - started) * 1000.0, "", raw, headers_out
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"error": _truncate(raw, 500)}
        headers_out = {str(k).lower(): str(v) for k, v in dict(exc.headers or {}).items()}
        return int(exc.code), _safe_dict(parsed), (time.perf_counter() - started) * 1000.0, "", raw, headers_out
    except Exception as exc:
        return None, {}, (time.perf_counter() - started) * 1000.0, str(exc), "", {}


def _run_live_preflight(
    *,
    base_url: str,
    timeout: float,
    headers: Mapping[str, str],
    auth_mode: str,
) -> dict[str, Any]:
    base = str(base_url).rstrip("/")
    health_status, health_payload, health_latency, health_err, health_raw, health_headers = _http_json_request(
        method="GET",
        url=f"{base}/health",
        timeout=timeout,
    )
    me_status, me_payload, me_latency, me_err, me_raw, me_headers = _http_json_request(
        method="GET",
        url=f"{base}/users/me",
        headers=headers,
        timeout=timeout,
    )

    failures: list[str] = []
    if health_status != 200:
        failures.append("health_unreachable")
    if me_status in {401, 403}:
        failures.append("auth_failure")
    elif me_status != 200:
        failures.append("users_me_unexpected_status")

    return {
        "auth_mode": auth_mode,
        "ok": not failures,
        "failures": failures,
        "health": {
            "status_code": health_status,
            "latency_ms": round(float(health_latency), 3),
            "error": str(health_err or ""),
            "request_id": _extract_request_id(health_payload, health_headers),
            "response_snippet": _response_snippet(health_payload, health_raw, health_err),
        },
        "users_me": {
            "status_code": me_status,
            "latency_ms": round(float(me_latency), 3),
            "error": str(me_err or ""),
            "request_id": _extract_request_id(me_payload, me_headers),
            "response_snippet": _response_snippet(me_payload, me_raw, me_err),
        },
    }


def _mock_latency(case: PerformanceCase, sample_index: int, *, profile: str) -> float:
    base_map = {
        "fast_message": 280.0,
        "no_search": 1400.0,
        "fast_search": 950.0,
        "entity_lookup": 3200.0,
        "news_search": 5200.0,
        "comparison_search": 6100.0,
        "doc_mode": 2400.0,
        "official_search": 7800.0,
        "deep_search": 8600.0,
    }
    base = base_map.get(case.expected_route, 2000.0)
    jitter = float((sample_index % 7) * 37 + (sample_index % 3) * 21)
    if case.id == "package_vite_cached":
        if profile == "cold":
            return base + 420.0 + jitter
        return base - 170.0 + jitter
    return base + jitter


def _mock_payload(case: PerformanceCase, sample_index: int, *, profile: str) -> tuple[int, dict[str, Any], float]:
    latency_ms = _mock_latency(case, sample_index, profile=profile)
    route = case.expected_route
    fallback = route in {"official_search", "deep_search"} and sample_index % 5 == 0
    payload = {
        "answer": f"Performance mock answer for {case.id}.",
        "route": route,
        "metadata": {
            "route_owner": _route_owner(route),
            "cache_profile": profile,
            "cache_hit": bool(case.id == "package_vite_cached" and profile == "warm"),
        },
        "trace": {
            "route_label": route,
            "timing": {"total_ms": latency_ms, "llm_calls": 1 if route != "package_source_of_record" else 0},
            "route_boundary_summary": {
                "route": route,
                "owner": _route_owner(route),
                "web_search_allowed": route in {"fast_search", "entity_lookup", "news_search", "official_search", "comparison_search", "deep_search"},
                "research_allowed": route in {"entity_lookup", "news_search", "official_search", "comparison_search", "deep_search"},
                "doc_pipeline_allowed": route == "doc_mode",
                "fsm_allowed": route == "task",
            },
            "planner_path": "entity_lookup" if route == "entity_lookup" else "deep_research" if route in {"news_search", "official_search", "comparison_search", "deep_search"} else route,
            "query_kind": "entity_lookup" if route == "entity_lookup" else route,
            "evidence_stats": {
                "search_calls": 0 if route in {"fast_message", "no_search", "doc_mode"} else 1 if route == "fast_search" else 2,
                "extract_count": 0 if route in {"fast_message", "no_search", "doc_mode"} else 1 if route in {"fast_search", "entity_lookup"} else 2,
                "cache_summary": {
                    "cache_hit": bool(case.id == "package_vite_cached" and profile == "warm"),
                    "cache_profile": profile,
                },
            },
            "provider_health": {
                "openrouter": {"state": "closed", "failures": 0, "fallback_used": fallback},
                "serper": {"state": "closed", "failures": 0, "fallback_used": False},
                "npm_registry": {
                    "state": "closed",
                    "failures": 0,
                    "fallback_used": False,
                    "cache_used": bool(case.id == "package_vite_cached" and profile == "warm"),
                },
            },
            "fallback_used": fallback,
        },
    }
    usage = _usage_from_payload(payload, route)
    quota = _quota_from_usage(usage)
    usage = _mark_usage_budget_if_needed(usage, quota)
    payload["usage"] = usage
    payload["quota"] = quota
    return 200, payload, latency_ms


def _build_live_request_row(
    *,
    case: PerformanceCase,
    sample_index: int,
    cache_profile: str,
    base_url: str,
    timeout: float,
    headers: Mapping[str, str],
    user_id: str,
    auth_mode: str,
    concurrency: int,
) -> dict[str, Any]:
    payload = {
        "query": case.query,
        "user_id": user_id,
        "include_trace": True,
        "tier": "enterprise",
    }
    status, body, latency_ms, transport_error, raw_body, response_headers = _http_json_request(
        method="POST",
        url=f"{str(base_url).rstrip('/')}/execute",
        headers=headers,
        payload=payload,
        timeout=timeout,
    )
    route = _observed_route(body, fallback="")
    metadata = _safe_dict(body.get("metadata"))
    owner = str(
        body.get("owner")
        or metadata.get("route_owner")
        or _safe_dict(body.get("detail")).get("owner")
        or _route_owner(route)
    )
    error_code = _extract_error_code(body, status, transport_error)
    request_id = _extract_request_id(body, response_headers)
    snippet = _response_snippet(body, raw_body, transport_error)
    failed_checks = _build_failed_checks(
        status_code=status,
        transport_error=transport_error,
        observed_route=route,
        expected_route=case.expected_route,
        error_code=error_code,
        request_id=request_id,
        response_snippet=snippet,
    )
    usage = _usage_from_payload(body, route or case.expected_route)
    quota = _safe_dict(body.get("quota")) or _quota_from_usage(usage)
    usage = _mark_usage_budget_if_needed(usage, quota)
    return {
        "case_id": case.id,
        "query": case.query,
        "expected_route": case.expected_route,
        "observed_route": route,
        "owner": owner,
        "status_code": status,
        "latency_ms": round(float(latency_ms), 3),
        "fallback_used": bool(_fallback_used(body)),
        "error": str(transport_error or ""),
        "error_code": error_code,
        "request_id": request_id,
        "response_snippet": snippet,
        "failed_checks": failed_checks,
        "is_error": bool(status is None or (status or 0) >= 400 or transport_error),
        "cache_profile": cache_profile,
        "concurrency_used": int(concurrency),
        "auth_mode": auth_mode,
        "sample_index": int(sample_index),
        "usage": usage,
        "quota": quota,
    }


def _build_mock_request_row(
    *,
    case: PerformanceCase,
    sample_index: int,
    cache_profile: str,
    concurrency: int,
) -> dict[str, Any]:
    status, payload, latency_ms = _mock_payload(case, sample_index, profile=cache_profile)
    route = _observed_route(payload, fallback=case.expected_route)
    metadata = _safe_dict(payload.get("metadata"))
    owner = str(metadata.get("route_owner") or _route_owner(route))
    usage = _usage_from_payload(payload, route)
    quota = _safe_dict(payload.get("quota")) or _quota_from_usage(usage)
    usage = _mark_usage_budget_if_needed(usage, quota)
    return {
        "case_id": case.id,
        "query": case.query,
        "expected_route": case.expected_route,
        "observed_route": route,
        "owner": owner,
        "status_code": status,
        "latency_ms": round(float(latency_ms), 3),
        "fallback_used": bool(_fallback_used(payload)),
        "error": "",
        "error_code": "",
        "request_id": "",
        "response_snippet": "",
        "failed_checks": [],
        "is_error": False,
        "cache_profile": cache_profile,
        "concurrency_used": int(concurrency),
        "auth_mode": "mock",
        "sample_index": int(sample_index),
        "usage": usage,
        "quota": quota,
    }


def _run_case_samples(
    case: PerformanceCase,
    *,
    live: bool,
    base_url: str,
    timeout: float,
    concurrency: int,
    warmup_requests: int,
    live_headers: Mapping[str, str],
    live_user_id: str,
    auth_mode: str,
) -> tuple[list[dict[str, Any]], dict[str, float], int, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    cache_profiles: dict[str, float] = {}
    warmup_failures: list[dict[str, Any]] = []
    profiles = ["normal"]
    if case.id == "package_vite_cached":
        profiles = ["cold", "warm"]

    warmup_executed = 0
    for profile in profiles:
        for warmup_index in range(max(0, warmup_requests)):
            warmup_executed += 1
            if live:
                warmup_row = _build_live_request_row(
                    case=case,
                    sample_index=warmup_index,
                    cache_profile=profile,
                    base_url=base_url,
                    timeout=timeout,
                    headers=live_headers,
                    user_id=live_user_id,
                    auth_mode=auth_mode,
                    concurrency=concurrency,
                )
            else:
                warmup_row = _build_mock_request_row(
                    case=case,
                    sample_index=warmup_index,
                    cache_profile=profile,
                    concurrency=concurrency,
                )
            if warmup_row.get("failed_checks"):
                warmup_failures.append(
                    {
                        "case_id": warmup_row.get("case_id"),
                        "query": warmup_row.get("query"),
                        "status_code": warmup_row.get("status_code"),
                        "latency_ms": warmup_row.get("latency_ms"),
                        "route": warmup_row.get("observed_route") or "unknown",
                        "owner": warmup_row.get("owner") or "unknown",
                        "error_code": warmup_row.get("error_code") or "",
                        "response_snippet": warmup_row.get("response_snippet") or "",
                        "request_id": warmup_row.get("request_id") or "",
                        "auth_mode": warmup_row.get("auth_mode") or auth_mode,
                        "failed_checks": list(warmup_row.get("failed_checks") or []),
                        "warmup": True,
                    }
                )

        profile_rows: list[dict[str, Any]] = []
        if live:
            worker_count = max(1, int(concurrency))
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = [
                    pool.submit(
                        _build_live_request_row,
                        case=case,
                        sample_index=i,
                        cache_profile=profile,
                        base_url=base_url,
                        timeout=timeout,
                        headers=live_headers,
                        user_id=live_user_id,
                        auth_mode=auth_mode,
                        concurrency=concurrency,
                    )
                    for i in range(case.request_count)
                ]
                for future in as_completed(futures):
                    profile_rows.append(dict(future.result()))
        else:
            for i in range(case.request_count):
                profile_rows.append(
                    _build_mock_request_row(
                        case=case,
                        sample_index=i,
                        cache_profile=profile,
                        concurrency=concurrency,
                    )
                )

        profile_rows.sort(key=lambda row: int(row.get("sample_index") or 0))
        rows.extend(profile_rows)
        profile_latencies = [float(row.get("latency_ms") or 0.0) for row in profile_rows]
        if profile_latencies:
            cache_profiles[profile] = round(sum(profile_latencies) / float(len(profile_latencies)), 3)
    return rows, cache_profiles, warmup_executed, warmup_failures


def _aggregate_routes(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        route = str(row.get("observed_route") or "unknown")
        target = grouped.setdefault(
            route,
            {
                "count": 0,
                "latencies": [],
                "fallbacks": 0,
                "errors": 0,
                "owner": str(row.get("owner") or _route_owner(route)),
                "estimated_cost_usd": 0.0,
                "llm_calls": 0,
                "search_calls": 0,
                "extract_calls": 0,
                "cache_hits": 0,
                "budget_exceeded": 0,
            },
        )
        target["count"] += 1
        target["latencies"].append(float(row.get("latency_ms") or 0.0))
        target["fallbacks"] += 1 if bool(row.get("fallback_used")) else 0
        target["errors"] += 1 if bool(row.get("is_error")) else 0
        usage = _safe_dict(row.get("usage"))
        target["estimated_cost_usd"] += float(usage.get("estimated_cost_usd") or 0.0)
        target["llm_calls"] += int(usage.get("llm_calls") or 0)
        target["search_calls"] += int(usage.get("search_calls") or 0)
        target["extract_calls"] += int(usage.get("extract_calls") or 0)
        target["cache_hits"] += int(usage.get("cache_hits") or 0)
        target["budget_exceeded"] += 1 if bool(usage.get("budget_exceeded")) else 0
    summary: dict[str, dict[str, Any]] = {}
    for route, data in grouped.items():
        count = max(1, int(data["count"]))
        summary[route] = {
            "count": int(data["count"]),
            "owner": data.get("owner"),
            "p50_ms": percentile(data["latencies"], 50),
            "p90_ms": percentile(data["latencies"], 90),
            "p95_ms": percentile(data["latencies"], 95),
            "fallback_rate": round(float(data["fallbacks"]) / float(count), 3),
            "error_rate": round(float(data["errors"]) / float(count), 3),
            "avg_estimated_cost_usd": round(float(data["estimated_cost_usd"]) / float(count), 6),
            "avg_llm_calls": round(float(data["llm_calls"]) / float(count), 3),
            "avg_search_calls": round(float(data["search_calls"]) / float(count), 3),
            "avg_extract_calls": round(float(data["extract_calls"]) / float(count), 3),
            "cache_hit_rate": round(float(data["cache_hits"]) / float(count), 3),
            "budget_exceeded_rate": round(float(data["budget_exceeded"]) / float(count), 3),
        }
    return dict(sorted(summary.items()))


def _budget_rows(case: PerformanceCase, case_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, float | None]]:
    warm_rows = [row for row in case_rows if str(row.get("cache_profile")) == "warm"]
    budget_source = warm_rows if warm_rows else case_rows
    success_source = [row for row in budget_source if not bool(row.get("is_error"))]
    pcts = calculate_percentiles(float(row.get("latency_ms") or 0.0) for row in success_source)
    observed_p95 = pcts.get("p95_ms")
    budget_passed = bool(observed_p95 is not None and float(observed_p95) <= float(case.max_p95_ms))
    usage_rows = [_safe_dict(row.get("usage")) for row in success_source]
    route_budget = _route_budget(case.expected_route)
    avg_cost = round(
        sum(float(row.get("estimated_cost_usd") or 0.0) for row in usage_rows) / float(max(1, len(usage_rows))),
        6,
    )
    return (
        {
            "id": case.id,
            "expected_route": case.expected_route,
            "route_owner": _route_owner(case.expected_route),
            "max_p95_ms": case.max_p95_ms,
            "observed_p95_ms": round(float(observed_p95), 3) if observed_p95 is not None else None,
            "budget_passed": bool(budget_passed),
            "successful_samples": len(success_source),
            "total_samples": len(budget_source),
            "avg_estimated_cost_usd": avg_cost,
            "budget_profile": route_budget,
        },
        pcts,
    )


def _failure_diagnostics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for row in rows:
        checks = list(row.get("failed_checks") or [])
        if not checks:
            continue
        failures.append(
            {
                "case_id": row.get("case_id"),
                "query": row.get("query"),
                "status_code": row.get("status_code"),
                "latency_ms": row.get("latency_ms"),
                "route": row.get("observed_route") or "unknown",
                "owner": row.get("owner") or "unknown",
                "error_code": row.get("error_code") or "",
                "response_snippet": row.get("response_snippet") or "",
                "request_id": row.get("request_id") or "",
                "auth_mode": row.get("auth_mode") or "",
                "failed_checks": checks,
            }
        )
    return failures


def _failed_check_counts(failures: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in failures:
        for check in row.get("failed_checks") or []:
            name = str(check or "").strip()
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def run_load_test(
    *,
    cases: list[PerformanceCase],
    live: bool = False,
    explicit_live: bool = False,
    base_url: str = "http://localhost:8000",
    max_cases: int | None = None,
    concurrency_override: int | None = None,
    timeout: float = 20.0,
    warmup_requests: int = 1,
    auth_token: str = "",
    dev_user_id: str = "perf_runner",
    perf_test_mode: bool = False,
    reliability_error_rate_budget: float = 0.02,
    allow_rate_limited_failures: bool = False,
) -> dict[str, Any]:
    if live and not explicit_live:
        raise ValueError("Live mode is blocked unless explicit_live=True (CLI --live).")

    selected = cases[: max_cases or None]
    effective_base_url = _normalize_base_url(base_url)
    headers, auth_mode = _auth_headers(
        auth_token=auth_token,
        dev_user_id=dev_user_id,
        perf_test_mode=perf_test_mode,
    )
    if live and auth_mode == "none":
        raise ValueError("Live mode requires --auth-token or --dev-user-id for reliable auth checks.")

    preflight: dict[str, Any] | None = None
    if live:
        preflight = _run_live_preflight(
            base_url=effective_base_url,
            timeout=timeout,
            headers=headers,
            auth_mode=auth_mode,
        )

    all_rows: list[dict[str, Any]] = []
    case_summaries: list[dict[str, Any]] = []
    cache_profile_summary: dict[str, dict[str, float]] = {}
    budget_failures: list[dict[str, Any]] = []
    warmup_executed_total = 0
    warmup_failures: list[dict[str, Any]] = []

    if preflight and not bool(preflight.get("ok")):
        all_failures = list(preflight.get("failures") or [])
        auth_failures = 1 if "auth_failure" in all_failures else 0
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "live",
            "base_url": base_url,
            "effective_base_url": effective_base_url,
            "warmup_requests_per_profile": int(max(0, warmup_requests)),
            "warmup_executed_total": 0,
            "preflight": preflight,
            "total_requests": 0,
            "passed": 0,
            "failed": 0,
            "error_rate": 1.0,
            "latency": {"p50_ms": None, "p90_ms": None, "p95_ms": None},
            "routes": {},
            "cases": [],
            "cache_profiles": {},
            "budget_failures": [],
            "route_budget_failures": [],
            "reliability": {
                "passed": False,
                "error_rate": 1.0,
                "error_rate_budget": reliability_error_rate_budget,
                "allow_rate_limited_failures": bool(allow_rate_limited_failures),
                "rate_limited_failures": 0,
                "unknown_route_failures": 0,
                "auth_failures": auth_failures,
                "raw_internal_errors": 0,
                "contract_failures": 0,
                "route_mismatch_failures": 0,
                "non_200_failures": 0,
                "failed_check_counts": {name: 1 for name in all_failures},
                "reason": "live_preflight_failed",
            },
            "latency_gate": {"passed": False, "reason": "live_preflight_failed"},
            "failures": [
                {
                    "case_id": "live_preflight",
                    "query": "",
                    "status_code": _safe_dict(preflight.get("users_me")).get("status_code"),
                    "latency_ms": _safe_dict(preflight.get("users_me")).get("latency_ms"),
                    "route": "unknown",
                    "owner": "unknown",
                    "error_code": "preflight_failed",
                    "response_snippet": _safe_dict(preflight.get("users_me")).get("response_snippet"),
                    "request_id": _safe_dict(preflight.get("users_me")).get("request_id"),
                    "auth_mode": auth_mode,
                    "failed_checks": all_failures or ["live_preflight_failed"],
                }
            ],
            "ok": False,
        }
        return report

    for case in selected:
        requested_concurrency = max(1, int(concurrency_override or case.concurrency))
        case_rows, cache_profiles, case_warmups, case_warmup_failures = _run_case_samples(
            case,
            live=live,
            base_url=effective_base_url,
            timeout=timeout,
            concurrency=requested_concurrency,
            warmup_requests=warmup_requests,
            live_headers=headers,
            live_user_id=str(dev_user_id or "perf_runner"),
            auth_mode=auth_mode if live else "mock",
        )
        warmup_executed_total += case_warmups
        warmup_failures.extend(case_warmup_failures)

        for row in case_rows:
            row["concurrency_requested"] = requested_concurrency
        all_rows.extend(case_rows)

        if "cold" in cache_profiles and "warm" in cache_profiles:
            cache_profile_summary[case.id] = {
                "cold_avg_ms": cache_profiles["cold"],
                "warm_avg_ms": cache_profiles["warm"],
                "warm_improvement_ms": round(cache_profiles["cold"] - cache_profiles["warm"], 3),
            }

        budget, pct = _budget_rows(case, case_rows)
        if not bool(budget.get("budget_passed")):
            budget_failures.append(budget)
        case_summaries.append(
            {
                **budget,
                "request_count": len(case_rows),
                "concurrency_requested": requested_concurrency,
                "concurrency_used": requested_concurrency,
                "p50_ms": pct["p50_ms"],
                "p90_ms": pct["p90_ms"],
            }
        )

    latencies = [float(row.get("latency_ms") or 0.0) for row in all_rows]
    percentiles = calculate_percentiles(latencies)
    failures = _failure_diagnostics(all_rows)
    failure_counts = _failed_check_counts(failures)
    total_requests = len(all_rows)
    error_count = sum(1 for row in all_rows if bool(row.get("is_error")))
    passed_count = total_requests - len(failures)
    failed_count = len(failures)
    error_rate = round(float(error_count) / float(max(1, total_requests)), 3)

    rate_limited_failures = sum(1 for row in failures if "rate_limited" in list(row.get("failed_checks") or []))
    unknown_route_failures = sum(
        1
        for row in failures
        if ("route_unknown" in list(row.get("failed_checks") or [])) or ("route_missing" in list(row.get("failed_checks") or []))
    )
    auth_failures = sum(1 for row in failures if "auth_failure" in list(row.get("failed_checks") or []))
    raw_internal_errors = sum(1 for row in failures if "internal_error" in list(row.get("failed_checks") or []))
    contract_failures = sum(1 for row in failures if "contract_failure" in list(row.get("failed_checks") or []))
    route_mismatch_failures = sum(1 for row in failures if "route_mismatch" in list(row.get("failed_checks") or []))
    non_200_failures = sum(1 for row in failures if "http_status" in list(row.get("failed_checks") or []))

    reliability_passed = bool(
        total_requests > 0
        and error_rate <= float(reliability_error_rate_budget)
        and (bool(allow_rate_limited_failures) or rate_limited_failures == 0)
        and unknown_route_failures == 0
        and auth_failures == 0
        and raw_internal_errors == 0
        and contract_failures == 0
        and route_mismatch_failures == 0
    )
    latency_passed = bool(total_requests > 0 and not budget_failures)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if live else "mock",
        "base_url": base_url if live else None,
        "effective_base_url": effective_base_url if live else None,
        "warmup_requests_per_profile": int(max(0, warmup_requests)),
        "warmup_executed_total": int(warmup_executed_total),
        "warmup_failures": warmup_failures,
        "preflight": preflight if live else None,
        "total_requests": total_requests,
        "passed": passed_count,
        "failed": failed_count,
        "error_rate": error_rate,
        "latency": percentiles,
        "routes": _aggregate_routes(all_rows),
        "cases": case_summaries,
        "route_budget_matrix": default_cost_policy().as_dict(),
        "cache_profiles": cache_profile_summary,
        "budget_failures": budget_failures,
        "route_budget_failures": budget_failures,
        "reliability": {
            "passed": reliability_passed,
            "error_rate": error_rate,
            "error_rate_budget": float(reliability_error_rate_budget),
            "allow_rate_limited_failures": bool(allow_rate_limited_failures),
            "rate_limited_failures": int(rate_limited_failures),
            "unknown_route_failures": int(unknown_route_failures),
            "auth_failures": int(auth_failures),
            "raw_internal_errors": int(raw_internal_errors),
            "contract_failures": int(contract_failures),
            "route_mismatch_failures": int(route_mismatch_failures),
            "non_200_failures": int(non_200_failures),
            "failed_check_counts": failure_counts,
        },
        "latency_gate": {
            "passed": latency_passed,
            "budget_failures": budget_failures,
        },
        "failures": failures,
        "records": all_rows,
        "ok": bool(reliability_passed and latency_passed),
    }
    return report


def run_ramp_mode(
    *,
    cases: list[PerformanceCase],
    live: bool = False,
    explicit_live: bool = False,
    base_url: str = "http://localhost:8000",
    max_cases: int | None = None,
    timeout: float = 20.0,
    warmup_requests: int = 1,
    auth_token: str = "",
    dev_user_id: str = "perf_runner",
    perf_test_mode: bool = False,
    reliability_error_rate_budget: float = 0.02,
    allow_rate_limited_failures: bool = False,
    concurrency_levels: Sequence[int] = (1, 2, 5),
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for concurrency in concurrency_levels:
        result = run_load_test(
            cases=cases,
            live=live,
            explicit_live=explicit_live,
            base_url=base_url,
            max_cases=max_cases,
            concurrency_override=int(concurrency),
            timeout=timeout,
            warmup_requests=warmup_requests,
            auth_token=auth_token,
            dev_user_id=dev_user_id,
            perf_test_mode=perf_test_mode,
            reliability_error_rate_budget=reliability_error_rate_budget,
            allow_rate_limited_failures=allow_rate_limited_failures,
        )
        result["ramp_concurrency"] = int(concurrency)
        runs.append(result)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if live else "mock",
        "ramp_mode": True,
        "ramp_profiles": [int(x) for x in concurrency_levels],
        "runs": runs,
        "ok": bool(runs and all(bool(row.get("ok")) for row in runs)),
    }


def _md_cell(value: Any) -> str:
    return _truncate(str(value or "").replace("|", "/"), 80)


def _render_single_markdown(report: Mapping[str, Any], heading: str = "# TAOS Performance Load Test") -> str:
    reliability = _safe_dict(report.get("reliability"))
    latency_gate = _safe_dict(report.get("latency_gate"))
    lines = [
        heading,
        "",
        f"- Generated: {report.get('generated_at')}",
        f"- Mode: `{report.get('mode')}`",
        f"- Total Requests: **{report.get('total_requests')}**",
        f"- Passed: **{report.get('passed')}**",
        f"- Failed: **{report.get('failed')}**",
        f"- Error Rate: **{report.get('error_rate')}**",
        f"- Latency P50/P90/P95 (ms): **{_safe_dict(report.get('latency')).get('p50_ms')} / {_safe_dict(report.get('latency')).get('p90_ms')} / {_safe_dict(report.get('latency')).get('p95_ms')}**",
        f"- Reliability Gate: **{'passed' if reliability.get('passed') else 'failed'}**",
        f"- Latency Gate: **{'passed' if latency_gate.get('passed') else 'failed'}**",
        f"- Warm-up Requests Excluded: **{report.get('warmup_executed_total')}**",
        "",
        "## Reliability Details",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| `error_rate` | {reliability.get('error_rate')} |",
        f"| `error_rate_budget` | {reliability.get('error_rate_budget')} |",
        f"| `allow_rate_limited_failures` | {reliability.get('allow_rate_limited_failures')} |",
        f"| `rate_limited_failures` | {reliability.get('rate_limited_failures')} |",
        f"| `unknown_route_failures` | {reliability.get('unknown_route_failures')} |",
        f"| `auth_failures` | {reliability.get('auth_failures')} |",
        f"| `raw_internal_errors` | {reliability.get('raw_internal_errors')} |",
        f"| `contract_failures` | {reliability.get('contract_failures')} |",
        f"| `route_mismatch_failures` | {reliability.get('route_mismatch_failures')} |",
        f"| `non_200_failures` | {reliability.get('non_200_failures')} |",
    ]

    preflight = _safe_dict(report.get("preflight"))
    if preflight:
        health = _safe_dict(preflight.get("health"))
        users = _safe_dict(preflight.get("users_me"))
        lines.extend(
            [
                "",
                "## Live Preflight",
                "",
                f"- Auth Mode: `{preflight.get('auth_mode')}`",
                f"- Preflight OK: **{preflight.get('ok')}**",
                f"- /health status: `{health.get('status_code')}`",
                f"- /users/me status: `{users.get('status_code')}`",
            ]
        )

    lines.extend(
        [
            "",
            "## Route Metrics",
            "",
            "| Route | Owner | Count | P50 ms | P95 ms | Avg cost USD | Fallback Rate | Error Rate |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for route, row in _safe_dict(report.get("routes")).items():
        lines.append(
            f"| `{route}` | `{row.get('owner')}` | {row.get('count')} | {row.get('p50_ms')} | {row.get('p95_ms')} | {row.get('avg_estimated_cost_usd')} | {row.get('fallback_rate')} | {row.get('error_rate')} |"
        )

    lines.extend(
        [
            "",
            "## Case Budgets",
            "",
            "| Case | Route | Owner | Concurrency | P95 ms | Budget ms | Avg cost USD | Successful Samples | Budget Passed |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in list(report.get("cases") or []):
        lines.append(
            f"| `{row.get('id')}` | `{row.get('expected_route')}` | `{row.get('route_owner')}` | {row.get('concurrency_used')} | "
            f"{row.get('observed_p95_ms')} | {row.get('max_p95_ms')} | {row.get('avg_estimated_cost_usd')} | {row.get('successful_samples')} | "
            f"{'yes' if row.get('budget_passed') else 'no'} |"
        )

    lines.extend(
        [
            "",
            "## Route Budget Matrix",
            "",
            "| Route | Owner | Tools | Research | P95 Budget ms | Max cost USD | Max search | Max extract |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for route, row in sorted(default_cost_policy().as_dict().items()):
        lines.append(
            f"| `{route}` | `{row.get('owner')}` | {row.get('uses_tools')} | {row.get('calls_research_pipeline')} | {row.get('max_latency_p95_ms')} | {row.get('max_estimated_cost_usd')} | {row.get('max_search_calls')} | {row.get('max_extract_calls')} |"
        )

    cache_profiles = _safe_dict(report.get("cache_profiles"))
    if cache_profiles:
        lines.extend(
            [
                "",
                "## Cache Warm vs Cold",
                "",
                "| Case | Cold Avg ms | Warm Avg ms | Improvement ms |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for case_id, row in cache_profiles.items():
            lines.append(
                f"| `{case_id}` | {row.get('cold_avg_ms')} | {row.get('warm_avg_ms')} | {row.get('warm_improvement_ms')} |"
            )

    failure_counts = _safe_dict(reliability.get("failed_check_counts"))
    if failure_counts:
        lines.extend(
            [
                "",
                "## Failure Profile",
                "",
                "| Failed Check | Count |",
                "| --- | ---: |",
            ]
        )
        for check, count in failure_counts.items():
            lines.append(f"| `{check}` | {count} |")

    failures = list(report.get("failures") or [])
    if failures:
        lines.extend(
            [
                "",
                "## Failure Samples",
                "",
                "| Case | Status | Route | Error Code | Failed Checks | Request ID | Snippet |",
                "| --- | ---: | --- | --- | --- | --- | --- |",
            ]
        )
        for row in failures[:20]:
            lines.append(
                f"| `{_md_cell(row.get('case_id'))}` | {row.get('status_code')} | `{_md_cell(row.get('route'))}` | "
                f"`{_md_cell(row.get('error_code'))}` | `{_md_cell(','.join(row.get('failed_checks') or []))}` | "
                f"`{_md_cell(row.get('request_id'))}` | `{_md_cell(row.get('response_snippet'))}` |"
            )
    return "\n".join(lines) + "\n"


def render_markdown(report: Mapping[str, Any]) -> str:
    if bool(report.get("ramp_mode")):
        runs = list(report.get("runs") or [])
        lines = [
            "# TAOS Performance Load Test (Ramp Mode)",
            "",
            f"- Generated: {report.get('generated_at')}",
            f"- Mode: `{report.get('mode')}`",
            f"- Overall OK: **{report.get('ok')}**",
            "",
            "## Ramp Summary",
            "",
            "| Concurrency | Reliability | Latency | Error Rate | Rate Limited | Unknown Route | Auth Failures | P95 ms | OK |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for run in runs:
            reliability = _safe_dict(run.get("reliability"))
            latency_gate = _safe_dict(run.get("latency_gate"))
            p95 = _safe_dict(run.get("latency")).get("p95_ms")
            lines.append(
                f"| {run.get('ramp_concurrency')} | "
                f"{'pass' if reliability.get('passed') else 'fail'} | "
                f"{'pass' if latency_gate.get('passed') else 'fail'} | "
                f"{reliability.get('error_rate')} | "
                f"{reliability.get('rate_limited_failures')} | "
                f"{reliability.get('unknown_route_failures')} | "
                f"{reliability.get('auth_failures')} | "
                f"{p95} | "
                f"{'pass' if run.get('ok') else 'fail'} |"
            )

        for run in runs:
            lines.append("")
            lines.append(_render_single_markdown(run, heading=f"## Concurrency {run.get('ramp_concurrency')}"))
        return "\n".join(lines).strip() + "\n"
    return _render_single_markdown(report)


def write_report(report: Mapping[str, Any], *, json_path: str | Path, md_path: str | Path) -> None:
    Path(json_path).write_text(json.dumps(dict(report), indent=2, sort_keys=True), encoding="utf-8")
    Path(md_path).write_text(render_markdown(report), encoding="utf-8")


def _parse_ramp_levels(value: str) -> list[int]:
    items = [chunk.strip() for chunk in str(value or "").split(",") if chunk.strip()]
    levels = [max(1, int(item)) for item in items]
    return levels or [1, 2, 5]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TAOS performance/load tests.")
    parser.add_argument("--cases", default=str(_REPO_ROOT / "qa" / "performance_cases.json"))
    parser.add_argument("--mock", action="store_true", help="Run deterministic mock mode.")
    parser.add_argument("--live", action="store_true", help="Run explicit live mode (required for real API calls).")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None, help="Override case concurrency for all cases.")
    parser.add_argument("--ramp-mode", action="store_true", help="Run sequential ramp profile for concurrency 1,2,5.")
    parser.add_argument("--ramp-levels", default="1,2,5", help="Comma-separated concurrency levels used with --ramp-mode.")
    parser.add_argument("--warmup-requests", type=int, default=1, help="Warm-up requests per case/profile excluded from metrics.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--auth-token", default="", help="Optional Bearer auth token for live mode.")
    parser.add_argument("--dev-user-id", default="perf_runner", help="Dev X-User-ID used for live mode (dev bypass).")
    parser.add_argument("--perf-test-mode", action="store_true", help="Send explicit dev perf-test mode header for elevated dev-only quota path.")
    parser.add_argument("--reliability-error-rate-budget", type=float, default=0.02)
    parser.add_argument("--allow-rate-limited-failures", action="store_true", help="Diagnostic-only: do not fail reliability gate for rate-limited failures.")
    parser.add_argument("--out-json", default=str(_REPO_ROOT / "PERFORMANCE_RESULTS.json"))
    parser.add_argument("--out-md", default=str(_REPO_ROOT / "PERFORMANCE_RESULTS.md"))
    args = parser.parse_args(argv)

    use_live = bool(args.live and not args.mock)
    cases = load_cases(args.cases)
    if args.ramp_mode:
        report = run_ramp_mode(
            cases=cases,
            live=use_live,
            explicit_live=bool(args.live),
            base_url=args.base_url,
            max_cases=args.max_cases,
            timeout=args.timeout,
            warmup_requests=max(0, int(args.warmup_requests)),
            auth_token=str(args.auth_token or ""),
            dev_user_id=str(args.dev_user_id or ""),
            perf_test_mode=bool(args.perf_test_mode),
            reliability_error_rate_budget=float(args.reliability_error_rate_budget),
            allow_rate_limited_failures=bool(args.allow_rate_limited_failures),
            concurrency_levels=_parse_ramp_levels(args.ramp_levels),
        )
    else:
        report = run_load_test(
            cases=cases,
            live=use_live,
            explicit_live=bool(args.live),
            base_url=args.base_url,
            max_cases=args.max_cases,
            concurrency_override=args.concurrency,
            timeout=args.timeout,
            warmup_requests=max(0, int(args.warmup_requests)),
            auth_token=str(args.auth_token or ""),
            dev_user_id=str(args.dev_user_id or ""),
            perf_test_mode=bool(args.perf_test_mode),
            reliability_error_rate_budget=float(args.reliability_error_rate_budget),
            allow_rate_limited_failures=bool(args.allow_rate_limited_failures),
        )
    write_report(report, json_path=args.out_json, md_path=args.out_md)
    print(render_markdown(report))
    return 0 if bool(report.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
