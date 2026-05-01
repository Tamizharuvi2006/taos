from __future__ import annotations

from taos.core.governance import CostPolicy, QuotaManager, UsageMeter
from taos.core.monitoring.ops_dashboard import build_ops_dashboard


def test_usage_meter_counts_llm_search_extract_calls() -> None:
    meter = UsageMeter(route="deep_search")
    meter.add_llm_call(tokens=1200)
    meter.add_search_call(count=4)
    meter.add_extract_call(count=5)
    meter.add_cache_hit(count=2)

    usage = meter.snapshot()
    assert usage.llm_calls == 1
    assert usage.llm_tokens == 1200
    assert usage.search_calls == 4
    assert usage.extract_calls == 5
    assert usage.cache_hits == 2
    assert usage.estimated_cost_usd > 0


def test_route_level_cost_budget_applies() -> None:
    usage = UsageMeter(route="fast_search")
    usage.add_search_call(count=3)

    decision = QuotaManager().check(usage.snapshot())

    assert decision.allowed is False
    assert "search_call_budget_exceeded" in decision.reasons


def test_deep_search_budget_exceeded_returns_controlled_response() -> None:
    meter = UsageMeter(route="deep_search")
    meter.add_search_call(count=8)
    response = QuotaManager().controlled_response(meter.snapshot())

    assert response["status"] == "budget_exceeded"
    assert "stopped safely" in response["message"]
    assert response["usage"]["route"] == "deep_search"


def test_package_lookup_remains_cheap_and_registry_only() -> None:
    meter = UsageMeter(route="package_source_of_record")
    meter.add_package_registry_call()
    decision = QuotaManager().check(meter.snapshot())

    assert decision.allowed is True
    assert meter.snapshot().search_calls == 0
    assert meter.snapshot().estimated_cost_usd <= CostPolicy().budget_for("package_source_of_record").max_estimated_cost_usd


def test_usage_metadata_from_trace_and_ops_dashboard() -> None:
    meter = UsageMeter.from_trace(
        "fast_search",
        {
            "timing": {"llm_calls": 1},
            "evidence_stats": {"source_type": "package_registry", "package_registry_used": True},
            "provider_health": {"npm_registry": {"cache_used": True}},
        },
    )
    usage = meter.to_dict()
    dashboard = build_ops_dashboard(execution_records=[{"route": "fast_search", "usage": usage}])

    assert usage["package_registry_calls"] == 1
    assert usage["cache_hits"] == 1
    assert dashboard["usage"]["package_registry_calls"] == 1
    assert dashboard["usage"]["routes"]["fast_search"]["count"] == 1


def test_live_eval_cost_limit_shape_is_explicit() -> None:
    budget = CostPolicy().budget_for("news_search")

    assert budget.owner == "research_pipeline"
    assert budget.max_latency_p95_ms is not None
    assert budget.max_search_calls >= 1
    assert budget.max_estimated_cost_usd > 0


def test_entity_lookup_budget_shape_is_explicit() -> None:
    budget = CostPolicy().budget_for("entity_lookup")

    assert budget.owner == "entity_lookup_pipeline"
    assert budget.uses_tools is True
    assert budget.calls_research_pipeline is False
    assert budget.max_latency_p95_ms == 6000
