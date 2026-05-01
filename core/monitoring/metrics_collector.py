from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Mapping

from taos.core.governance.cost_policy import default_cost_policy


RESEARCH_ROUTES = {"deep_search", "deep_research", "news_search", "official_search", "comparison_search"}
DEFAULT_PROVIDERS = ("openrouter", "serper", "npm_registry", "web_extract", "firebase")


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _number(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if numeric == numeric else default


def _ratio(value: Any, default: float = 0.0) -> float:
    numeric = _number(value, default)
    if numeric > 1.0:
        numeric = numeric / 100.0
    return max(0.0, min(1.0, numeric))


def percentile(values: Iterable[float], percentile_value: float) -> float | None:
    clean = sorted(float(v) for v in values if _number(v, None) is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return round(clean[0], 3)
    rank = (len(clean) - 1) * (percentile_value / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(clean) - 1)
    weight = rank - lower
    return round(clean[lower] * (1.0 - weight) + clean[upper] * weight, 3)


def _pick_route(record: Mapping[str, Any]) -> str:
    trace = _as_dict(record.get("trace"))
    metadata = _as_dict(record.get("metadata"))
    telemetry = _as_dict(record.get("route_telemetry"))
    return str(
        record.get("route")
        or record.get("route_label")
        or record.get("observed_route")
        or record.get("observed_type")
        or telemetry.get("route_label")
        or telemetry.get("observed_type")
        or trace.get("route_label")
        or metadata.get("route")
        or "unknown"
    )

def _pick_owner(record: Mapping[str, Any]) -> str:
    trace = _as_dict(record.get("trace"))
    metadata = _as_dict(record.get("metadata"))
    usage = _as_dict(record.get("usage"))
    route_boundary = _as_dict(trace.get("route_boundary_summary"))
    return str(
        record.get("owner")
        or usage.get("route_owner")
        or metadata.get("route_owner")
        or route_boundary.get("owner")
        or trace.get("route_owner")
        or "unknown"
    )


def _pick_latency(record: Mapping[str, Any]) -> float:
    trace = _as_dict(record.get("trace"))
    timing = _as_dict(trace.get("timing"))
    return _number(
        record.get("latency_ms")
        or record.get("elapsed_ms")
        or timing.get("total_ms")
        or timing.get("time_to_final_ms")
        or 0.0
    )


def _pick_provider_health(record: Mapping[str, Any]) -> Dict[str, Any]:
    trace = _as_dict(record.get("trace"))
    metadata = _as_dict(record.get("metadata"))
    return _as_dict(record.get("provider_health") or trace.get("provider_health") or metadata.get("provider_health"))


def _pick_evidence(record: Mapping[str, Any]) -> Dict[str, Any]:
    trust = _as_dict(record.get("trust_block"))
    metadata = _as_dict(record.get("metadata"))
    trace = _as_dict(record.get("trace"))
    return _as_dict(
        record.get("evidence_matrix_summary")
        or trust.get("evidence_matrix_summary")
        or metadata.get("evidence_matrix_summary")
        or trace.get("evidence_matrix_summary")
    )


def _pick_freshness(record: Mapping[str, Any]) -> Dict[str, Any]:
    trust = _as_dict(record.get("trust_block"))
    metadata = _as_dict(record.get("metadata"))
    trace = _as_dict(record.get("trace"))
    return _as_dict(record.get("freshness_summary") or trust.get("freshness_summary") or metadata.get("freshness_summary") or trace.get("freshness_summary"))


def _pick_answer_mode(record: Mapping[str, Any]) -> str:
    trust = _as_dict(record.get("trust_block"))
    metadata = _as_dict(record.get("metadata"))
    evidence_stats = _as_dict(metadata.get("evidence_stats"))
    return str(record.get("answer_mode") or trust.get("answer_mode") or evidence_stats.get("answer_mode") or "unknown").lower()


def _fallback_used(record: Mapping[str, Any]) -> bool:
    if bool(record.get("fallback_used")):
        return True
    trace = _as_dict(record.get("trace"))
    metadata = _as_dict(record.get("metadata"))
    if bool(trace.get("fallback_used") or metadata.get("fallback_used")):
        return True
    for state in _pick_provider_health(record).values():
        if isinstance(state, Mapping) and bool(state.get("fallback_used")):
            return True
    return False


def _source_unavailable(record: Mapping[str, Any]) -> int:
    metadata = _as_dict(record.get("metadata"))
    if bool(record.get("source_unavailable") or metadata.get("source_unavailable")):
        return 1
    count = 0
    provider_health = _pick_provider_health(record)
    npm = _as_dict(provider_health.get("npm_registry"))
    count += int(_number(npm.get("source_unavailable_count"), 0))
    if bool(npm.get("source_unavailable")):
        count += 1
    return count


def _stage_timings(record: Mapping[str, Any]) -> List[Dict[str, Any]]:
    trace = _as_dict(record.get("trace"))
    stages = _as_list(record.get("stage_timings") or trace.get("stage_timings") or trace.get("stage_budget_timings"))
    return [dict(stage) for stage in stages if isinstance(stage, Mapping)]


def _pick_usage(record: Mapping[str, Any]) -> Dict[str, Any]:
    trace = _as_dict(record.get("trace"))
    metadata = _as_dict(record.get("metadata"))
    return _as_dict(record.get("usage") or trace.get("usage") or metadata.get("usage"))


def _pick_budget(record: Mapping[str, Any]) -> Dict[str, Any]:
    trace = _as_dict(record.get("trace"))
    metadata = _as_dict(record.get("metadata"))
    return _as_dict(record.get("quota") or trace.get("quota") or metadata.get("quota"))


def _collect_route_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    buckets: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "latencies": [],
            "errors": 0,
            "fallbacks": 0,
            "coverages": [],
            "owners": Counter(),
            "estimated_cost_usd": 0.0,
            "llm_calls": 0,
            "search_calls": 0,
            "extract_calls": 0,
            "cache_hits": 0,
            "budget_exceeded": 0,
        }
    )
    for record in records:
        route = _pick_route(record)
        bucket = buckets[route]
        bucket["count"] += 1
        bucket["owners"][_pick_owner(record)] += 1
        latency = _pick_latency(record)
        if latency > 0:
            bucket["latencies"].append(latency)
        status = str(record.get("status") or "").lower()
        if status in {"failed", "error"} or bool(record.get("error")):
            bucket["errors"] += 1
        if _fallback_used(record):
            bucket["fallbacks"] += 1
        evidence = _pick_evidence(record)
        raw_coverage = evidence.get("citation_coverage") or evidence.get("coverage") or record.get("coverage")
        if raw_coverage is not None:
            bucket["coverages"].append(_ratio(raw_coverage, 0.0))
        usage = _pick_usage(record)
        if usage:
            bucket["estimated_cost_usd"] += _number(usage.get("estimated_cost_usd"), 0.0)
            bucket["llm_calls"] += int(_number(usage.get("llm_calls"), 0))
            bucket["search_calls"] += int(_number(usage.get("search_calls"), 0))
            bucket["extract_calls"] += int(_number(usage.get("extract_calls"), 0))
            bucket["cache_hits"] += int(_number(usage.get("cache_hits"), 0))
            if bool(usage.get("budget_exceeded")):
                bucket["budget_exceeded"] += 1

    routes = {}
    for route, bucket in sorted(buckets.items()):
        count = max(1, int(bucket["count"]))
        latencies = bucket["latencies"]
        coverages = bucket["coverages"]
        primary_owner = bucket["owners"].most_common(1)[0][0] if bucket["owners"] else "unknown"
        row = {
            "count": bucket["count"],
            "avg_latency_ms": round(sum(latencies) / len(latencies), 3) if latencies else None,
            "p50_ms": percentile(latencies, 50) if latencies else None,
            "p90_ms": percentile(latencies, 90) if latencies else None,
            "p95_ms": percentile(latencies, 95) if latencies else None,
            "error_rate": round(bucket["errors"] / count, 3),
            "fallback_rate": round(bucket["fallbacks"] / count, 3),
            "owner": primary_owner,
            "avg_estimated_cost_usd": round(bucket["estimated_cost_usd"] / count, 6),
            "avg_llm_calls": round(bucket["llm_calls"] / count, 3),
            "avg_search_calls": round(bucket["search_calls"] / count, 3),
            "avg_extract_calls": round(bucket["extract_calls"] / count, 3),
            "cache_hit_rate": round(bucket["cache_hits"] / count, 3),
            "budget_exceeded_rate": round(bucket["budget_exceeded"] / count, 3),
        }
        if coverages and route in RESEARCH_ROUTES:
            row["coverage_avg"] = round(sum(coverages) / len(coverages), 3)
        routes[route] = row
    return routes


def _collect_provider_metrics(records: List[Dict[str, Any]], snapshots: Iterable[Mapping[str, Any]] | None) -> Dict[str, Any]:
    providers: Dict[str, Dict[str, Any]] = {
        name: {"state": "unknown", "failures": 0, "fallback_count": 0, "cache_hit_count": 0, "source_unavailable_count": 0}
        for name in DEFAULT_PROVIDERS
    }

    rows = list(snapshots or [])
    rows.extend(_pick_provider_health(record) for record in records)
    for row in rows:
        for provider, state_raw in _as_dict(row).items():
            state = _as_dict(state_raw)
            target = providers.setdefault(
                provider,
                {"state": "unknown", "failures": 0, "fallback_count": 0, "cache_hit_count": 0, "source_unavailable_count": 0},
            )
            if state.get("state"):
                target["state"] = str(state.get("state"))
            target["failures"] += int(_number(state.get("failures") or state.get("failure_count"), 0))
            target["fallback_count"] += int(_number(state.get("fallback_count"), 0)) + (1 if state.get("fallback_used") else 0)
            target["cache_hit_count"] += int(_number(state.get("cache_hit_count"), 0)) + (1 if state.get("cache_used") else 0)
            target["source_unavailable_count"] += int(_number(state.get("source_unavailable_count"), 0)) + (1 if state.get("source_unavailable") else 0)
    return providers


def _collect_research_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    coverages: List[float] = []
    freshness_scores: List[float] = []
    answer_modes: Counter[str] = Counter()
    conflict_count = 0
    unsupported_critical_total = 0
    source_unavailable_count = 0

    for record in records:
        route = _pick_route(record)
        mode = _pick_answer_mode(record)
        evidence = _pick_evidence(record)
        freshness = _pick_freshness(record)
        is_research = bool(evidence) or mode != "unknown" or bool(freshness) or record.get("coverage") is not None
        if not is_research:
            source_unavailable_count += _source_unavailable(record)
            continue
        raw_coverage = record.get("coverage") or evidence.get("coverage") or evidence.get("citation_coverage")
        if raw_coverage is not None:
            coverage = _ratio(raw_coverage, 0.0)
            coverages.append(coverage)
        freshness_score = _ratio(record.get("freshness_score") or freshness.get("freshness_score"), 0.0)
        if freshness_score:
            freshness_scores.append(freshness_score)
        answer_modes[mode] += 1
        if bool(record.get("conflict_detected") or _as_dict(record.get("conflict_summary")).get("conflict_detected")):
            conflict_count += 1
        unsupported_critical_total += int(_number(record.get("unsupported_critical_claims") or evidence.get("unsupported_critical_claims"), 0))
        source_unavailable_count += _source_unavailable(record)

    low_coverage_count = sum(1 for value in coverages if value < 0.75)
    return {
        "avg_coverage": round(sum(coverages) / len(coverages), 3) if coverages else None,
        "low_coverage_count": low_coverage_count,
        "avg_freshness": round(sum(freshness_scores) / len(freshness_scores), 3) if freshness_scores else None,
        "conflict_detected_count": conflict_count,
        "unsupported_critical_claims": unsupported_critical_total,
        "answer_modes": dict(sorted(answer_modes.items())),
        "source_of_record_unavailable_count": source_unavailable_count,
    }


def _collect_latency_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    latencies = [_pick_latency(record) for record in records if _pick_latency(record) > 0]
    route_latencies: Dict[str, List[float]] = defaultdict(list)
    slowest_stage = None
    slowest_stage_ms = -1.0
    for record in records:
        latency = _pick_latency(record)
        if latency > 0:
            route_latencies[_pick_route(record)].append(latency)
        for stage in _stage_timings(record):
            duration = _number(stage.get("duration_ms"), 0)
            if duration > slowest_stage_ms:
                slowest_stage_ms = duration
                slowest_stage = str(stage.get("stage") or "unknown")
    slowest_route = None
    slowest_route_avg = -1.0
    for route, values in route_latencies.items():
        avg = sum(values) / len(values)
        if avg > slowest_route_avg:
            slowest_route = route
            slowest_route_avg = avg
    return {
        "p50_ms": percentile(latencies, 50),
        "p90_ms": percentile(latencies, 90),
        "p95_ms": percentile(latencies, 95),
        "slowest_route": slowest_route,
        "slowest_stage": slowest_stage,
        "sample_count": len(latencies),
    }


def _collect_usage_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    totals = {
        "llm_calls": 0,
        "search_calls": 0,
        "extract_calls": 0,
        "package_registry_calls": 0,
        "cache_hits": 0,
        "fallback_count": 0,
        "estimated_cost_usd": 0.0,
        "budget_exceeded_count": 0,
    }
    by_route: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "estimated_cost_usd": 0.0,
            "budget_exceeded_count": 0,
            "llm_calls": 0,
            "search_calls": 0,
            "extract_calls": 0,
            "package_registry_calls": 0,
            "cache_hits": 0,
            "fallback_count": 0,
            "owners": Counter(),
        }
    )
    for record in records:
        usage = _pick_usage(record)
        if not usage:
            continue
        route = str(usage.get("route") or _pick_route(record) or "unknown")
        route_bucket = by_route[route]
        route_bucket["count"] += 1
        route_bucket["owners"][str(usage.get("route_owner") or _pick_owner(record) or "unknown")] += 1
        for key in ("llm_calls", "search_calls", "extract_calls", "package_registry_calls", "cache_hits", "fallback_count"):
            totals[key] += int(_number(usage.get(key), 0))
            route_bucket[key] += int(_number(usage.get(key), 0))
        cost = _number(usage.get("estimated_cost_usd"), 0.0)
        totals["estimated_cost_usd"] += cost
        route_bucket["estimated_cost_usd"] += cost
        if bool(usage.get("budget_exceeded")):
            totals["budget_exceeded_count"] += 1
            route_bucket["budget_exceeded_count"] += 1
    totals["estimated_cost_usd"] = round(float(totals["estimated_cost_usd"]), 6)
    budget_matrix = default_cost_policy().as_dict()
    return {
        **totals,
        "routes": {
            route: {
                **{k: v for k, v in row.items() if k != "owners"},
                "owner": row["owners"].most_common(1)[0][0] if row["owners"] else "unknown",
                "estimated_cost_usd": round(float(row.get("estimated_cost_usd") or 0.0), 6),
                "avg_estimated_cost_usd": round(float(row.get("estimated_cost_usd") or 0.0) / max(1, int(row.get("count") or 1)), 6),
                "avg_llm_calls": round(float(row.get("llm_calls") or 0.0) / max(1, int(row.get("count") or 1)), 3),
                "avg_search_calls": round(float(row.get("search_calls") or 0.0) / max(1, int(row.get("count") or 1)), 3),
                "avg_extract_calls": round(float(row.get("extract_calls") or 0.0) / max(1, int(row.get("count") or 1)), 3),
                "cache_hit_rate": round(float(row.get("cache_hits") or 0.0) / max(1, int(row.get("count") or 1)), 3),
            }
            for route, row in sorted(by_route.items())
        },
        "budget_matrix": budget_matrix,
    }


def collect_ops_metrics(
    *,
    execution_records: Iterable[Mapping[str, Any]] | None = None,
    provider_snapshots: Iterable[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    records = [dict(record) for record in list(execution_records or []) if isinstance(record, Mapping)]
    return {
        "routes": _collect_route_metrics(records),
        "providers": _collect_provider_metrics(records, provider_snapshots),
        "research": _collect_research_metrics(records),
        "latency": _collect_latency_metrics(records),
        "usage": _collect_usage_metrics(records),
    }
