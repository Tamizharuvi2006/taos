from __future__ import annotations

from taos.core.routing.global_hybrid_router import GlobalHybridRouter
from taos.core.routing.route_cache import RouteCache
from taos.core.routing.route_decider import RouteDecider
from taos.core.understanding.universal_understanding_gateway import (
    UniversalUnderstandingGateway,
    frame_to_trace_summary,
)


def _decide(query: str):
    RouteCache().clear()
    return RouteDecider().decide_sync_for_tests(query)


def test_fast_message_typo_routes_fast_message() -> None:
    frame = UniversalUnderstandingGateway().understand("heyy buddyy")
    summary = frame_to_trace_summary(frame)
    assert summary["original_query"] == "heyy buddyy"
    assert summary["normalized_query"] == "hey buddy"
    assert summary["route_hint"] == "fast_message"
    assert _decide("heyy buddyy").route == "fast_message"


def test_no_search_explanation_typo_routes_no_search() -> None:
    decision = _decide("explain js closur simple")
    signals = decision.routing_signals["universal_understanding"]
    assert decision.route == "no_search"
    assert signals["normalized_query"] == "explain JavaScript closures simply"
    assert signals["original_query"] == "explain js closur simple"


def test_package_version_typo_routes_fast_search_source_of_record_hint() -> None:
    decision = _decide("curent vite versio")
    signals = decision.routing_signals["universal_understanding"]
    assert decision.route == "fast_search"
    assert signals["relation"] == "latest_version"
    assert signals["entities"]["tool_framework"] == "Vite"
    assert signals["raw_query_priority"] == "fallback_only"


def test_rumour_claim_builds_query_plan_before_research() -> None:
    decision = _decide("india lovking claude rumour")
    signals = decision.routing_signals["universal_understanding"]
    plan = signals["query_plan_summary"]
    assert decision.route == "news_search"
    assert signals["normalized_question"] == "Is Claude blocked or restricted in India?"
    assert plan["lanes"]["official"]
    assert plan["lanes"]["news"]
    assert plan["lanes"]["contradiction"]
    assert plan["lanes"]["background"]
    assert plan["lanes"]["official"][0] != "india lovking claude rumour"


def test_document_exam_query_routes_doc_mode() -> None:
    decision = _decide("in this pdf give imprtnt 16 marks")
    signals = decision.routing_signals["universal_understanding"]
    assert decision.route == "doc_mode"
    assert signals["document_hints"]["mode"] == "important_questions"
    assert signals["document_hints"]["mark_format"] == "16_mark"


def test_messy_reminder_routes_task_with_time_hints() -> None:
    decision = _decide("remind me tomorw mrng 8")
    signals = decision.routing_signals["universal_understanding"]
    assert decision.route == "task"
    assert signals["task_hints"]["action"] == "reminder"
    assert signals["task_hints"]["date_hint"] == "tomorrow"
    assert signals["task_hints"]["time_of_day"] == "morning"
    assert "tomorrow morning at 8" in signals["normalized_query"]


def test_code_help_typo_normalizes_module_not_found() -> None:
    decision = _decide("fix modu not fond react")
    signals = decision.routing_signals["universal_understanding"]
    assert decision.route == "task"
    assert signals["code_hints"]["error_type"] == "module_not_found"
    assert signals["code_hints"]["framework"] == "React"
    assert signals["normalized_query"] == "fix module not found error in React"


def test_comparison_typo_routes_comparison_search() -> None:
    decision = _decide("react vs anglr whch better")
    signals = decision.routing_signals["universal_understanding"]
    assert decision.route == "comparison_search"
    assert "Angular" in signals["normalized_query"]
    assert signals["intent_hint"] == "comparison"


def test_low_confidence_ambiguous_input_routes_clarification() -> None:
    decision = _decide("asdf qwer")
    signals = decision.routing_signals["universal_understanding"]
    assert decision.route == "clarification"
    assert "low_confidence" in signals["ambiguity_flags"]


def test_global_hybrid_router_consumes_universal_frame_context() -> None:
    frame = UniversalUnderstandingGateway().understand("remind me tomorw mrng 8")
    summary = frame_to_trace_summary(frame)
    decision = GlobalHybridRouter().route("remind me tomorw mrng 8", context={"universal_understanding": summary})
    assert decision.route == "task"
    assert decision.signals["normalized_query"] == "remind me tomorrow morning at 8"
