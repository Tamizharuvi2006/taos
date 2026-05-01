from __future__ import annotations

from taos.core.routing.route_decider import RouteDecider
from taos.core.routing.route_rules import deterministic_route
from taos.orchestration.engine import OrchestrationEngine


def _observe(query: str, selected_route: str = "entity_lookup") -> dict:
    return OrchestrationEngine()._build_query_frame_observation(query=query, selected_route=selected_route)


def test_query_frame_trace_observe_for_ceo_lookup() -> None:
    observed = _observe("who is the ceo of relyce infotech")
    assert observed["intent_family"] == "entity_lookup"
    assert observed["requested_role"] == "ceo"
    assert observed["entity_name"].lower() == "relyce infotech"
    assert observed["route_alignment"] == "aligned"


def test_query_frame_trace_observe_for_founder_lookup() -> None:
    observed = _observe("who founded microsoft")
    assert observed["intent_family"] == "entity_lookup"
    assert observed["requested_role"] == "founder"
    assert observed["entity_name"].lower() == "microsoft"
    assert observed["route_alignment"] == "aligned"


def test_query_frame_trace_observe_for_linkedin_lookup() -> None:
    observed = _observe("find linkedin of relyce infotech")
    assert observed["profile_target"] == "linkedin_profile"
    assert observed["entity_name"].lower() == "relyce infotech"
    assert observed["route_alignment"] == "aligned"


def test_query_frame_trace_observe_for_official_website_lookup() -> None:
    observed = _observe("relyce infotech official website")
    assert observed["profile_target"] == "official_website"
    assert observed["entity_name"].lower() == "relyce infotech"
    assert observed["route_alignment"] == "aligned"


def test_query_frame_trace_observe_for_business_legitimacy_lookup() -> None:
    observed = _observe("is relyce infotech a real company")
    assert observed["profile_target"] == "business_legitimacy"
    assert observed["entity_name"].lower() == "relyce infotech"
    assert observed["route_alignment"] == "aligned"


def test_query_frame_observe_only_does_not_change_selected_route() -> None:
    observed = _observe("who is the ceo of relyce infotech", selected_route="fast_search")
    assert observed["current_selected_route"] == "fast_search"
    assert observed["route_alignment"] == "mismatch"


def test_existing_route_behavior_remains_entity_lookup() -> None:
    decider = RouteDecider()
    for query in (
        "who is the ceo of relyce infotech",
        "who founded microsoft",
        "find linkedin of relyce infotech",
        "relyce infotech official website",
        "is relyce infotech a real company",
    ):
        baseline = deterministic_route(query, {})
        if baseline is not None:
            assert baseline.route in {"entity_lookup", "official_search"}
        decided = decider.decide_sync_for_tests(query, context={})
        observed = _observe(query, selected_route=decided.route)
        assert observed["current_selected_route"] == decided.route
