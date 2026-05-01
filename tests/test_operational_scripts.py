from __future__ import annotations

from pathlib import Path

from taos.scripts.build_ops_dashboard import build_dashboard
from taos.scripts.check_route_integrity import evaluate_route_integrity


def test_check_route_integrity_passes_matching_routes():
    report = {
        "results": [
            {
                "case_id": "case_1",
                "route_telemetry": {
                    "case_id": "case_1",
                    "expected_type": "deep_research",
                    "observed_type": "deep_research",
                    "route_label": "deep_research",
                    "owner": "research_pipeline",
                    "matched_expected": True,
                },
            }
        ]
    }
    failures, summary = evaluate_route_integrity(report)
    assert failures == []
    assert summary["route_match_rate"] == 1.0


def test_check_route_integrity_fails_mismatches():
    report = {
        "results": [
            {
                "case_id": "case_1",
                "route_telemetry": {
                    "case_id": "case_1",
                    "expected_type": "doc_mode",
                    "observed_type": "standard_task",
                    "route_label": "standard_task",
                    "owner": "planner",
                    "matched_expected": False,
                },
            }
        ]
    }
    failures, summary = evaluate_route_integrity(report)
    assert summary["route_mismatch_count"] == 1
    assert any("case_1" in failure for failure in failures)


def test_build_ops_dashboard_from_eval_report(tmp_path: Path):
    report_path = tmp_path / "intelligence.json"
    report_path.write_text(
        """
{
  "overall_score": 0.91,
  "completed_count": 2,
  "failed_count": 0,
  "metric_averages": {"citation_quality": 0.8},
  "recommendations": ["Keep weekly regression runs."],
  "route_telemetry": {
    "route_match_rate": 1.0,
    "route_mismatch_count": 0,
    "fallback_count": 0,
    "route_counts": {"deep_research": 1},
    "owner_counts": {"research_pipeline": 1}
  },
  "results": []
}
""".strip(),
        encoding="utf-8",
    )
    dashboard = build_dashboard(intelligence_report=report_path)
    assert dashboard["quality"]["overall_score"] == 0.91
    assert dashboard["routing"]["route_match_rate"] == 1.0
    assert dashboard["external_readiness"]["target_deployment_validation"] == "external_required"
