from __future__ import annotations

from taos.core.search.query_planner_v2 import SearchQueryPlannerV2
from taos.core.search.source_lane_router import SourceLaneRouter


def test_query_planner_outputs_structured_lanes_for_rumour() -> None:
    plan = SearchQueryPlannerV2().plan("I heard India blocked Claude is it true")
    assert plan.intent == "rumour_verification"
    assert plan.lane("official").required is True
    assert plan.lane("contradiction").required is True
    assert plan.lane("background").required is True
    assert "Anthropic Claude supported countries India" in plan.lane("official").queries
    assert "Claude available in India Anthropic" in plan.lane("contradiction").queries


def test_raw_query_is_low_priority_fallback_for_messy_query() -> None:
    query = "research that indai lovking claudde rumour"
    plan = SearchQueryPlannerV2().plan(query)
    flattened = plan.flatten()
    assert plan.raw_query_priority == "fallback_only"
    assert flattened[0] != query
    assert flattened[-1] == query


def test_official_lane_required_for_pricing_and_release_claims() -> None:
    pricing = SearchQueryPlannerV2().plan("firebase pricing changed")
    release = SearchQueryPlannerV2().plan("latest vite release")
    assert pricing.lane("official").required is True
    assert release.lane("official").required is True


def test_source_lane_router_marks_required_lanes() -> None:
    router = SourceLaneRouter()
    lanes = router.lanes_for(intent="rumour_verification", relation="blocked_or_restricted_access", has_country=True)
    assert "official" in lanes
    assert "contradiction" in lanes
    assert "background" in lanes
    assert "regional" in lanes


def test_trace_summary_includes_query_plan_metadata() -> None:
    summary = SearchQueryPlannerV2().trace_summary("I heard India blocked Claude")
    assert summary["intent"] == "rumour_verification"
    assert "lanes" in summary
    assert "official" in summary["lanes"]
    assert summary["metadata"]["relation"] == "blocked_or_restricted_access"
