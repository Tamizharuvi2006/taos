from __future__ import annotations

import json
from pathlib import Path

from taos.core.monitoring.metrics_collector import collect_ops_metrics, percentile
from taos.core.monitoring.ops_dashboard import build_ops_dashboard, render_ops_dashboard_markdown
from taos.scripts.build_ops_dashboard import build_dashboard


def _sample_records():
    return [
        {
            "route": "fast_search",
            "latency_ms": 1200,
            "fallback_used": False,
            "provider_health": {"npm_registry": {"state": "closed", "cache_used": True}},
        },
        {
            "route": "fast_search",
            "latency_ms": 1800,
            "fallback_used": True,
            "source_unavailable": True,
            "provider_health": {"npm_registry": {"state": "closed", "source_unavailable": True}},
        },
        {
            "route": "deep_search",
            "latency_ms": 10000,
            "answer_mode": "best_supported",
            "freshness_score": 0.8,
            "conflict_detected": True,
            "unsupported_critical_claims": 0,
            "evidence_matrix_summary": {"citation_coverage": 0.82, "claims_supported": 4, "claims_total": 5},
            "stage_timings": [{"stage": "web_extract", "duration_ms": 5200}],
            "provider_health": {"serper": {"state": "closed", "fallback_used": True}},
        },
        {
            "route": "comparison_search",
            "latency_ms": 14000,
            "answer_mode": "weak_candidate",
            "freshness_score": 0.5,
            "unsupported_critical_claims": 1,
            "evidence_matrix_summary": {"citation_coverage": 0.42, "unsupported_critical_claims": 1},
            "stage_timings": [{"stage": "answer_generation", "duration_ms": 2400}],
        },
    ]


def test_percentile_computation():
    assert percentile([100, 200, 300, 400], 50) == 250
    assert percentile([100, 200, 300, 400], 90) == 370


def test_builds_dashboard_json_from_sample_metrics():
    dashboard = build_ops_dashboard(execution_records=_sample_records())
    assert dashboard["route_health"]["fast_search"]["count"] == 2
    assert dashboard["route_health"]["deep_search"]["coverage_avg"] == 0.82
    assert dashboard["provider_health"]["npm_registry"]["cache_hit_count"] == 1


def test_builds_dashboard_markdown_from_json():
    dashboard = build_ops_dashboard(execution_records=_sample_records())
    markdown = render_ops_dashboard_markdown(dashboard)
    assert "# TAOS Operational Dashboard" in markdown
    assert "## Route Health" in markdown
    assert "fast_search" in markdown
    assert "## Provider Health" in markdown


def test_handles_missing_provider_health_safely():
    dashboard = build_ops_dashboard(execution_records=[{"route": "no_search", "latency_ms": 500}])
    assert dashboard["provider_health"]["openrouter"]["state"] == "unknown"
    assert dashboard["provider_health"]["serper"]["failures"] == 0


def test_computes_route_fallback_rates():
    metrics = collect_ops_metrics(execution_records=_sample_records())
    assert metrics["routes"]["fast_search"]["fallback_rate"] == 0.5
    assert metrics["routes"]["deep_search"]["fallback_rate"] == 1.0


def test_computes_research_answer_mode_counts_and_low_coverage():
    metrics = collect_ops_metrics(execution_records=_sample_records())
    assert metrics["research"]["answer_modes"]["best_supported"] == 1
    assert metrics["research"]["answer_modes"]["weak_candidate"] == 1
    assert metrics["research"]["low_coverage_count"] == 1
    assert metrics["research"]["unsupported_critical_claims"] == 1


def test_computes_latency_p50_p90_p95_and_slowest_stage():
    metrics = collect_ops_metrics(execution_records=_sample_records())
    assert metrics["latency"]["p50_ms"] == 5900
    assert metrics["latency"]["p90_ms"] == 12800
    assert metrics["latency"]["p95_ms"] == 13400
    assert metrics["latency"]["slowest_stage"] == "web_extract"


def test_includes_source_of_record_unavailable_count():
    metrics = collect_ops_metrics(execution_records=_sample_records())
    assert metrics["research"]["source_of_record_unavailable_count"] == 1
    assert metrics["providers"]["npm_registry"]["source_unavailable_count"] == 1


def test_dashboard_script_writes_latest_json_and_md_files(tmp_path: Path):
    intelligence_report = tmp_path / "intelligence.json"
    research_report = tmp_path / "research.json"
    execution_records = tmp_path / "records.json"
    out_json = tmp_path / "ops_dashboard_latest.json"
    out_md = tmp_path / "ops_dashboard_latest.md"
    intelligence_report.write_text(
        json.dumps(
            {
                "overall_score": 0.91,
                "completed_count": 2,
                "failed_count": 0,
                "route_telemetry": {"route_match_rate": 1.0, "route_mismatch_count": 0, "fallback_count": 0},
            }
        ),
        encoding="utf-8",
    )
    research_report.write_text(
        json.dumps(
            {
                "aggregates": {"overall_score": 0.92, "pass_rate": 1.0},
                "results": [{"route": "deep_search", "latency_ms": 9000, "coverage": 0.8, "answer_mode": "best_supported"}],
            }
        ),
        encoding="utf-8",
    )
    execution_records.write_text(json.dumps({"records": _sample_records()}), encoding="utf-8")
    dashboard = build_dashboard(
        intelligence_report=intelligence_report,
        research_report=research_report,
        execution_records_path=execution_records,
    )
    out_json.write_text(json.dumps(dashboard, indent=2), encoding="utf-8")
    out_md.write_text(render_ops_dashboard_markdown(dashboard), encoding="utf-8")

    assert out_json.exists()
    assert out_md.exists()
    loaded = json.loads(out_json.read_text(encoding="utf-8"))
    assert loaded["quality"]["overall_score"] == 0.91
    assert loaded["research"]["latest_eval_overall_score"] == 0.92
