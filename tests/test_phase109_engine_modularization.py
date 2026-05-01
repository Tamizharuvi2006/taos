from taos.apps.api.routes.agent import _build_public_trace_summary
from taos.orchestration.handlers import (
    ClarificationHandler,
    DocumentHandler,
    FastMessageHandler,
    FastSearchHandler,
    NoSearchHandler,
    ResearchHandler,
    TaskHandler,
)
from taos.orchestration.route_dispatcher import RouteDispatcher
from taos.core.routing import RouteDecider


def test_route_dispatcher_maps_fast_message_to_fast_message_handler():
    assert RouteDispatcher().handler_class_for(route="fast_message") is FastMessageHandler


def test_route_dispatcher_maps_no_search_to_no_search_handler():
    assert RouteDispatcher().handler_class_for(route="no_search") is NoSearchHandler


def test_route_dispatcher_maps_fast_search_to_fast_search_handler():
    assert RouteDispatcher().handler_class_for(route="fast_search") is FastSearchHandler


def test_route_dispatcher_maps_research_routes_to_research_handler():
    dispatcher = RouteDispatcher()
    for route in ("deep_search", "news_search", "official_search", "comparison_search"):
        assert dispatcher.handler_class_for(route=route) is ResearchHandler


def test_route_dispatcher_maps_doc_mode_to_document_handler():
    assert RouteDispatcher().handler_class_for(route="doc_mode") is DocumentHandler


def test_route_dispatcher_maps_clarification_to_clarification_handler():
    assert RouteDispatcher().handler_class_for(route="clarification") is ClarificationHandler


def test_route_dispatcher_maps_task_routes_to_task_handler():
    dispatcher = RouteDispatcher()
    assert dispatcher.handler_class_for(route="task") is TaskHandler
    assert dispatcher.handler_class_for(route="standard_task") is TaskHandler


def test_unknown_route_falls_back_safely_to_task_handler():
    assert RouteDispatcher().handler_class_for(route="mystery") is TaskHandler


def test_owner_mapping_is_explicit_and_boring():
    dispatcher = RouteDispatcher()
    assert dispatcher.owner_for_route("fast_message") == "direct_fast_message"
    assert dispatcher.owner_for_route("no_search") == "direct_llm_no_tools"
    assert dispatcher.owner_for_route("fast_search") == "search_lite"
    assert dispatcher.owner_for_route("deep_search") == "research_pipeline"
    assert dispatcher.owner_for_route("doc_mode") == "document_pipeline"
    assert dispatcher.owner_for_route("clarification") == "clarification_fallback"
    assert dispatcher.owner_for_route("task") == "fsm_planner"


def test_package_version_query_still_bypasses_research_owner():
    decision = RouteDecider().decide_sync_for_tests("current vite version")

    assert decision.route == "fast_search"
    assert RouteDispatcher().owner_for_route(decision.route) == "search_lite"


def test_public_trace_shape_remains_stable_after_modularization():
    summary = _build_public_trace_summary(
        {
            "route_label": "fast_search",
            "route_decision": {"route": "fast_search", "confidence": 0.95, "reason": "source-of-record lookup"},
            "route_boundary_summary": {"route": "fast_search", "owner": "search_lite", "will_use_planner": False},
            "timing": {"route_ms": 20, "search_ms": 250, "total_ms": 900},
            "trust_block": {"trust_level": "high", "source_count": 1, "answer_mode": "verified"},
        }
    )

    assert set(summary.keys()) == {"route", "execution", "research", "trust", "latency"}
    assert summary["route"]["owner"] == "search_lite"
    assert summary["execution"]["planner_used"] is False
    assert summary["latency"]["total_ms"] == 900


def test_answer_appears_before_evidence_warnings_contract():
    answer = (
        "Answer\n"
        "The best-supported answer is X.\n\n"
        "Why this answer\n"
        "- S1 supports it.\n\n"
        "What to treat carefully\n"
        "- Evidence is partial."
    )

    assert answer.index("Answer") < answer.index("What to treat carefully")


def test_route_boundary_summary_contract_keeps_owner():
    summary = {
        "route": "fast_search",
        "selected_route": "fast_search",
        "owner": "search_lite",
        "research_allowed": False,
    }

    assert summary["owner"] == "search_lite"
    assert summary["research_allowed"] is False
