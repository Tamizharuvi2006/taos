from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from .metrics_collector import collect_ops_metrics


def load_json(path: str | Path | None) -> Dict[str, Any]:
    if not path:
        return {}
    json_path = Path(path)
    if not json_path.exists():
        return {}
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def parse_research_markdown(path: str | Path | None) -> Dict[str, Any]:
    if not path:
        return {}
    md_path = Path(path)
    if not md_path.exists():
        return {}
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    aggregates: Dict[str, Any] = {}
    metric_map = {
        "Overall Score": "overall_score",
        "Pass Rate": "pass_rate",
        "Route Accuracy": "route_accuracy",
        "Answer Utility": "answer_utility",
        "Source Quality": "source_quality",
        "Groundedness": "groundedness_score",
        "Freshness Score": "freshness_score",
        "Latency Score": "latency_score",
    }
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 2 or cells[0] not in metric_map:
            continue
        try:
            aggregates[metric_map[cells[0]]] = float(cells[1])
        except ValueError:
            continue
    failed_cases = []
    in_failed = False
    for line in text.splitlines():
        if line.strip() == "## Failed Cases":
            in_failed = True
            continue
        if in_failed and line.startswith("## "):
            break
        if in_failed and line.strip().startswith("- ") and "None" not in line:
            failed_cases.append(line.strip()[2:])
    return {"aggregates": aggregates, "failed_cases": failed_cases, "source": str(md_path)}


def records_from_intelligence_report(report: Mapping[str, Any]) -> list[Dict[str, Any]]:
    records = []
    for row in list(report.get("results") or []):
        if not isinstance(row, Mapping):
            continue
        response = row.get("response") if isinstance(row.get("response"), Mapping) else {}
        trace = response.get("trace") if isinstance(response.get("trace"), Mapping) else {}
        timing = trace.get("timing") if isinstance(trace.get("timing"), Mapping) else {}
        telemetry = row.get("route_telemetry") if isinstance(row.get("route_telemetry"), Mapping) else {}
        records.append(
            {
                "route": row.get("observed_type") or telemetry.get("route_label") or response.get("route") or trace.get("route_label"),
                "status": "failed" if row.get("status") == "failed" else "ok",
                "latency_ms": response.get("elapsed_ms") or timing.get("total_ms") or timing.get("time_to_final_ms"),
                "fallback_used": bool(telemetry.get("llm_fallback_used") or trace.get("fallback_used")),
                "route_telemetry": telemetry,
                "trace": trace,
                "metadata": response.get("metadata") if isinstance(response.get("metadata"), Mapping) else {},
            }
        )
    return records


def records_from_research_report(report: Mapping[str, Any]) -> list[Dict[str, Any]]:
    records = []
    for row in list(report.get("results") or []):
        if not isinstance(row, Mapping):
            continue
        records.append(
            {
                "route": row.get("route") or row.get("observed_route"),
                "latency_ms": row.get("latency_ms"),
                "coverage": row.get("coverage") or row.get("citation_coverage"),
                "freshness_score": row.get("freshness_score"),
                "answer_mode": row.get("answer_mode"),
                "fallback_used": row.get("fallback_usefulness", 0) > 0 and row.get("answer_mode") in {"weak_candidate", "no_usable_evidence"},
                "conflict_detected": row.get("conflict_detected"),
                "unsupported_critical_claims": row.get("unsupported_critical_claims"),
                "source_unavailable": row.get("source_unavailable"),
                "evidence_matrix_summary": {
                    "citation_coverage": row.get("coverage") or row.get("citation_coverage"),
                    "unsupported_critical_claims": row.get("unsupported_critical_claims"),
                },
            }
        )
    return records


def build_ops_dashboard(
    *,
    execution_records: Iterable[Mapping[str, Any]] | None = None,
    intelligence_report: Mapping[str, Any] | None = None,
    research_report: Mapping[str, Any] | None = None,
    provider_snapshots: Iterable[Mapping[str, Any]] | None = None,
    feedback_records: Iterable[Mapping[str, Any]] | None = None,
    inputs: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    intelligence = dict(intelligence_report or {})
    research_eval = dict(research_report or {})
    records = list(execution_records or [])
    records.extend(records_from_intelligence_report(intelligence))
    records.extend(records_from_research_report(research_eval))
    metrics = collect_ops_metrics(execution_records=records, provider_snapshots=provider_snapshots)
    aggregates = dict(research_eval.get("aggregates") or {})
    pass_rate = aggregates.get("pass_rate")
    if pass_rate is None and aggregates.get("cases_run"):
        failed = len(list(aggregates.get("failed_cases") or []))
        pass_rate = round((float(aggregates.get("cases_run") or 0) - failed) / float(aggregates.get("cases_run") or 1), 3)
    route_telemetry = dict(intelligence.get("route_telemetry") or {})
    feedback_rows = [dict(row or {}) for row in feedback_records or []]
    feedback_counts: Dict[str, int] = {}
    for row in feedback_rows:
        key = str(row.get("feedback_type") or "unknown")
        feedback_counts[key] = feedback_counts.get(key, 0) + 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": dict(inputs or {}),
        "external_readiness": {
            "target_deployment_validation": "external_required",
            "provider_cost_dashboard": "external_required",
            "error_monitoring_dashboard": "external_required",
            "frontend_workspace_render_check": "external_required",
            "local_intelligence_eval": "available" if intelligence else "missing",
            "local_research_eval": "available" if research_eval else "missing",
        },
        "quality": {
            "overall_score": float(intelligence.get("overall_score") or 0.0),
            "completed_count": int(intelligence.get("completed_count") or intelligence.get("completed") or 0),
            "failed_count": int(intelligence.get("failed_count") or intelligence.get("failed") or 0),
            "metric_averages": dict(intelligence.get("metric_averages") or {}),
            "recommendations": list(intelligence.get("recommendations") or []),
        },
        "routing": {
            "route_match_rate": route_telemetry.get("route_match_rate"),
            "route_mismatch_count": int(route_telemetry.get("route_mismatch_count") or 0),
            "fallback_count": int(route_telemetry.get("fallback_count") or 0),
            "case_count": int(route_telemetry.get("case_count") or len(records) or 0),
            "route_counts": dict(route_telemetry.get("route_counts") or {}),
            "owner_counts": dict(route_telemetry.get("owner_counts") or {}),
        },
        "route_health": metrics["routes"],
        "provider_health": metrics["providers"],
        "usage": metrics.get("usage", {}),
        "cost_performance": {
            "slowest_routes": [
                {"route": route, **row}
                for route, row in sorted(
                    metrics["routes"].items(),
                    key=lambda item: float(item[1].get("p95_ms") or 0.0),
                    reverse=True,
                )[:5]
            ],
            "most_expensive_routes": [
                {"route": route, **row}
                for route, row in sorted(
                    (metrics.get("usage", {}) or {}).get("routes", {}).items(),
                    key=lambda item: float(item[1].get("avg_estimated_cost_usd") or 0.0),
                    reverse=True,
                )[:5]
            ],
            "budget_matrix": (metrics.get("usage", {}) or {}).get("budget_matrix", {}),
        },
        "feedback": {
            "total": len(feedback_rows),
            "feedback_counts": feedback_counts,
            "automatic_behavior_change": False,
        },
        "research": {
            **metrics["research"],
            "latest_eval_overall_score": aggregates.get("overall_score"),
            "latest_eval_pass_rate": pass_rate,
            "latest_eval_failed_cases": list(research_eval.get("failed_cases") or aggregates.get("failed_cases") or []),
            "latest_eval_weak_areas": list(aggregates.get("weak_areas") or []),
        },
        "latency": metrics["latency"],
    }


def render_ops_dashboard_markdown(dashboard: Mapping[str, Any]) -> str:
    quality = dict(dashboard.get("quality") or {})
    routing = dict(dashboard.get("routing") or {})
    route_health = dict(dashboard.get("route_health") or {})
    provider_health = dict(dashboard.get("provider_health") or {})
    usage = dict(dashboard.get("usage") or {})
    cost_performance = dict(dashboard.get("cost_performance") or {})
    research = dict(dashboard.get("research") or {})
    feedback = dict(dashboard.get("feedback") or {})
    latency = dict(dashboard.get("latency") or {})
    readiness = dict(dashboard.get("external_readiness") or {})
    lines = [
        "# TAOS Operational Dashboard",
        "",
        f"Generated: {dashboard.get('generated_at')}",
        "",
        "## Quality",
        "",
        f"- Intelligence eval score: {float(quality.get('overall_score') or 0.0):.3f}",
        f"- Research eval score: {research.get('latest_eval_overall_score') if research.get('latest_eval_overall_score') is not None else 'not available'}",
        f"- Research pass rate: {research.get('latest_eval_pass_rate') if research.get('latest_eval_pass_rate') is not None else 'not available'}",
        f"- Feedback records: {int(feedback.get('total') or 0)}",
        "",
        "## Route Health",
        "",
        "| Route | Owner | Count | P50 ms | P95 ms | Error rate | Fallback rate | Avg cost USD | Coverage avg |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    if route_health:
        for route, row in sorted(route_health.items()):
            lines.append(
                f"| {route} | {row.get('owner', 'unknown')} | {row.get('count', 0)} | {row.get('p50_ms')} | {row.get('p95_ms')} | {row.get('error_rate')} | {row.get('fallback_rate')} | {row.get('avg_estimated_cost_usd')} | {row.get('coverage_avg', '')} |"
            )
    else:
        lines.append("| none | unknown | 0 |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Provider Health",
            "",
            "| Provider | State | Failures | Fallbacks | Cache hits | Source unavailable |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for provider, row in sorted(provider_health.items()):
        lines.append(
            f"| {provider} | {row.get('state', 'unknown')} | {row.get('failures', 0)} | {row.get('fallback_count', 0)} | {row.get('cache_hit_count', 0)} | {row.get('source_unavailable_count', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Research Quality",
            "",
            f"- Average coverage: {research.get('avg_coverage')}",
            f"- Low coverage count: {research.get('low_coverage_count', 0)}",
            f"- Average freshness: {research.get('avg_freshness')}",
            f"- Conflict detected count: {research.get('conflict_detected_count', 0)}",
            f"- Unsupported critical claims: {research.get('unsupported_critical_claims', 0)}",
            f"- Source-of-record unavailable count: {research.get('source_of_record_unavailable_count', 0)}",
            "",
            "## Usage / Cost",
            "",
            f"- LLM calls: {usage.get('llm_calls', 0)}",
            f"- Search calls: {usage.get('search_calls', 0)}",
            f"- Extract calls: {usage.get('extract_calls', 0)}",
            f"- Package registry calls: {usage.get('package_registry_calls', 0)}",
            f"- Cache hits: {usage.get('cache_hits', 0)}",
            f"- Estimated cost USD: {usage.get('estimated_cost_usd', 0.0)}",
            f"- Budget exceeded count: {usage.get('budget_exceeded_count', 0)}",
            "",
            "### Slowest Routes",
            "",
            "| Route | Owner | P95 ms | Avg cost USD | Budget exceeded rate |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    slowest_routes = list(cost_performance.get("slowest_routes") or [])
    if slowest_routes:
        for row in slowest_routes:
            lines.append(
                f"| {row.get('route')} | {row.get('owner', 'unknown')} | {row.get('p95_ms')} | {row.get('avg_estimated_cost_usd')} | {row.get('budget_exceeded_rate')} |"
            )
    else:
        lines.append("| none | unknown |  |  |  |")
    lines.extend(
        [
            "",
            "### Route Budget Matrix",
            "",
            "| Route | Owner | Tools | Research | P95 Budget ms | Max cost USD | Max search | Max extract |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for route, row in sorted((cost_performance.get("budget_matrix") or {}).items()):
        lines.append(
            f"| {route} | {row.get('owner')} | {row.get('uses_tools')} | {row.get('calls_research_pipeline')} | {row.get('max_latency_p95_ms')} | {row.get('max_estimated_cost_usd')} | {row.get('max_search_calls')} | {row.get('max_extract_calls')} |"
        )
    lines.extend(
        [
            "",
            "### Answer Modes",
            "",
        ]
    )
    answer_modes = dict(research.get("answer_modes") or {})
    if answer_modes:
        for mode, count in sorted(answer_modes.items()):
            lines.append(f"- {mode}: {count}")
    else:
        lines.append("- none recorded")
    lines.extend(
        [
            "",
            "## Latency",
            "",
            f"- p50 ms: {latency.get('p50_ms')}",
            f"- p90 ms: {latency.get('p90_ms')}",
            f"- p95 ms: {latency.get('p95_ms')}",
            f"- Slowest route: {latency.get('slowest_route')}",
            f"- Slowest stage: {latency.get('slowest_stage')}",
            "",
            "## External Readiness",
            "",
        ]
    )
    for key, value in sorted(readiness.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Routing Eval",
            "",
            f"- Route telemetry cases: {routing.get('case_count', 0)}",
            f"- Route match rate: {routing.get('route_match_rate') if routing.get('route_match_rate') is not None else 'not available'}",
            f"- Route mismatches: {routing.get('route_mismatch_count', 0)}",
            f"- Fallback count: {routing.get('fallback_count', 0)}",
            "",
            "## Recommendations",
            "",
        ]
    )
    recommendations = list(quality.get("recommendations") or [])
    if recommendations:
        for rec in recommendations:
            lines.append(f"- {rec}")
    else:
        lines.append("- No local recommendations available.")
    lines.append("")
    return "\n".join(lines)


def write_ops_dashboard(dashboard: Mapping[str, Any], *, out_json: str | Path, out_md: str | Path) -> None:
    json_path = Path(out_json)
    md_path = Path(out_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(dict(dashboard), indent=2), encoding="utf-8")
    md_path.write_text(render_ops_dashboard_markdown(dashboard), encoding="utf-8")
