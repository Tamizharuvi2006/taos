from __future__ import annotations

from taos.core.governance import CostPolicy, QuotaManager, UsageMeter
from taos.core.monitoring.ops_dashboard import build_ops_dashboard
from taos.scripts.run_performance_load_test import load_cases, run_load_test


def test_direct_routes_do_not_call_tools_in_budget_matrix() -> None:
    policy = CostPolicy()

    fast_message = policy.budget_for("fast_message")
    no_search = policy.budget_for("no_search")

    assert fast_message.uses_tools is False
    assert fast_message.max_search_calls == 0
    assert no_search.uses_tools is False
    assert no_search.max_search_calls == 0


def test_fast_search_budget_stays_outside_research_pipeline() -> None:
    budget = CostPolicy().budget_for("fast_search")

    assert budget.owner == "search_lite"
    assert budget.calls_research_pipeline is False
    assert budget.max_search_calls <= 2


def test_usage_snapshot_preserves_route_boundary_metadata() -> None:
    usage = UsageMeter.from_trace(
        "entity_lookup",
        {
            "timing": {"total_ms": 3210, "llm_calls": 1},
            "route_boundary_summary": {
                "owner": "entity_lookup_pipeline",
                "web_search_allowed": True,
                "research_allowed": True,
                "doc_pipeline_allowed": False,
                "fsm_allowed": False,
            },
            "planner_path": "entity_lookup",
            "query_kind": "entity_lookup",
            "evidence_stats": {"search_calls": 2, "extract_count": 1},
            "provider_health": {"serper": {"cache_used": True}},
        },
    ).to_dict()

    assert usage["route_owner"] == "entity_lookup_pipeline"
    assert usage["entity_pipeline_called"] is True
    assert usage["web_search_allowed"] is True
    assert usage["cache_hits"] == 1


def test_ops_dashboard_includes_cost_and_budget_summary() -> None:
    dashboard = build_ops_dashboard(
        execution_records=[
            {
                "route": "fast_search",
                "owner": "search_lite",
                "usage": {
                    "route": "fast_search",
                    "route_owner": "search_lite",
                    "llm_calls": 1,
                    "search_calls": 1,
                    "extract_calls": 1,
                    "package_registry_calls": 0,
                    "cache_hits": 1,
                    "fallback_count": 0,
                    "estimated_cost_usd": 0.007,
                    "budget_exceeded": False,
                    "latency_ms": 900,
                    "web_search_allowed": True,
                    "research_allowed": False,
                    "doc_pipeline_allowed": False,
                    "fsm_allowed": False,
                    "entity_pipeline_called": False,
                    "doc_pipeline_called": False,
                    "fsm_called": False,
                },
            }
        ]
    )

    assert "cost_performance" in dashboard
    assert dashboard["cost_performance"]["budget_matrix"]["fast_search"]["owner"] == "search_lite"
    assert dashboard["route_health"]["fast_search"]["owner"] == "search_lite"


def test_performance_runner_reports_route_level_budget_details() -> None:
    report = run_load_test(cases=load_cases("qa/performance_cases.json"), live=False, max_cases=4)

    assert report["cases"]
    case = report["cases"][0]
    assert "budget_profile" in case
    assert "route_owner" in case
    assert "avg_estimated_cost_usd" in case
    assert report["route_budget_matrix"]["doc_mode"]["owner"] == "document_pipeline"


def test_quota_manager_budget_matrix_is_available() -> None:
    matrix = QuotaManager().budget_matrix()

    assert "entity_lookup" in matrix
    assert matrix["entity_lookup"]["owner"] == "entity_lookup_pipeline"
