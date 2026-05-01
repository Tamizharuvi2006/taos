from __future__ import annotations

from taos.apps.api.response_contract import normalize_contract_payload
from taos.apps.api.routes.agent import _build_public_trace_summary, _build_trace
from taos.orchestration.engine import OrchestrationEngine


def _payload_for_route(*, phase107_route: str, selected_route: str, owner: str) -> dict:
    return {
        "answer": f"Answer for {phase107_route}",
        "direct_answer": f"Answer for {phase107_route}",
        "sections": [],
        "key_points": [],
        "sources": [],
        "route": "standard_task",
        "route_label": "standard_task",
        "mode": "standard_task",
        "trace": {
            "route_label": "standard_task",
            "planner_path": selected_route,
            "route_decision": {
                "route": phase107_route,
                "selected_route": selected_route,
                "route_owner": owner,
                "confidence": 0.93,
                "reason": "phase140_trace_label_test",
            },
            "route_boundary_summary": {
                "route": phase107_route,
                "selected_route": selected_route,
                "owner": owner,
                "boundary": "deterministic",
                "planner_allowed": phase107_route == "task",
                "fsm_allowed": phase107_route == "task",
                "web_search_allowed": phase107_route in {"fast_search", "news_search", "official_search", "comparison_search", "entity_lookup"},
                "research_allowed": phase107_route in {"news_search", "official_search", "comparison_search", "entity_lookup"},
                "doc_pipeline_allowed": phase107_route == "doc_mode",
            },
            "timing": {"total_ms": 42},
        },
        "route_decision": {
            "route": phase107_route,
            "selected_route": selected_route,
            "route_owner": owner,
            "confidence": 0.93,
            "reason": "phase140_trace_label_test",
        },
        "route_boundary_summary": {
            "route": phase107_route,
            "selected_route": selected_route,
            "owner": owner,
            "boundary": "deterministic",
            "planner_allowed": phase107_route == "task",
            "fsm_allowed": phase107_route == "task",
            "web_search_allowed": phase107_route in {"fast_search", "news_search", "official_search", "comparison_search", "entity_lookup"},
            "research_allowed": phase107_route in {"news_search", "official_search", "comparison_search", "entity_lookup"},
            "doc_pipeline_allowed": phase107_route == "doc_mode",
        },
        "metadata": {
            "route_label": "standard_task",
            "route_owner": owner,
        },
    }


def test_route_label_mapping_keeps_clarification_and_doc_mode_public():
    engine = OrchestrationEngine()

    assert engine._route_label_from_selected_route("clarification") == "clarification"
    assert engine._route_label_from_selected_route("doc_mode") == "doc_mode"
    assert engine._route_label_from_selected_route("entity_lookup") == "entity_lookup"
    assert engine._route_label_from_selected_route("fast_search") == "fast_search"
    assert engine._route_label_from_selected_route("no_search") == "no_search"
    assert engine._route_label_from_selected_route("micro_fast") == "fast_message"


def test_clarification_payload_normalizes_public_route_fields():
    payload = normalize_contract_payload(
        _payload_for_route(
            phase107_route="clarification",
            selected_route="clarification",
            owner="clarification_fallback",
        )
    )

    assert payload["public_route_label"] == "clarification"
    assert payload["selected_route"] == "clarification"
    assert payload["original_route_hint"] == "clarification"
    assert payload["route_label"] == "clarification"
    assert payload["route_owner"] == "clarification_fallback"


def test_doc_mode_payload_normalizes_public_route_fields():
    payload = normalize_contract_payload(
        _payload_for_route(
            phase107_route="doc_mode",
            selected_route="doc_mode",
            owner="document_pipeline",
        )
    )

    assert payload["public_route_label"] == "doc_mode"
    assert payload["selected_route"] == "doc_mode"
    assert payload["original_route_hint"] == "doc_mode"
    assert payload["route_label"] == "doc_mode"
    assert payload["route_owner"] == "document_pipeline"


def test_fast_search_payload_normalizes_public_route_fields():
    payload = normalize_contract_payload(
        _payload_for_route(
            phase107_route="fast_search",
            selected_route="fast_search",
            owner="search_lite",
        )
    )

    assert payload["public_route_label"] == "fast_search"
    assert payload["selected_route"] == "fast_search"
    assert payload["route_owner"] == "search_lite"


def test_entity_lookup_payload_normalizes_public_route_fields():
    payload = normalize_contract_payload(
        _payload_for_route(
            phase107_route="entity_lookup",
            selected_route="entity_lookup",
            owner="entity_lookup_pipeline",
        )
    )

    assert payload["public_route_label"] == "entity_lookup"
    assert payload["selected_route"] == "entity_lookup"
    assert payload["route_owner"] == "entity_lookup_pipeline"


def test_news_search_preserves_original_route_hint_when_execution_deepens():
    payload = normalize_contract_payload(
        _payload_for_route(
            phase107_route="news_search",
            selected_route="deep_research",
            owner="research_pipeline",
        )
    )

    assert payload["public_route_label"] == "deep_research"
    assert payload["selected_route"] == "deep_research"
    assert payload["original_route_hint"] == "news_search"
    assert payload["route_owner"] == "research_pipeline"


def test_public_trace_summary_prefers_public_route_label_over_stale_standard_task():
    payload = normalize_contract_payload(
        _payload_for_route(
            phase107_route="clarification",
            selected_route="clarification",
            owner="clarification_fallback",
        )
    )
    trace = _build_trace(payload["trace"], "req_phase140")

    assert trace is not None
    assert trace.public_route_label == "clarification"
    assert trace.selected_route == "clarification"
    assert trace.original_route_hint == "clarification"
    assert trace.route_owner == "clarification_fallback"

    summary = _build_public_trace_summary(trace.model_dump())
    assert summary["route"]["label"] == "clarification"
    assert summary["route"]["selected_route"] == "clarification"
    assert summary["route"]["original_route_hint"] == "clarification"
    assert summary["route"]["owner"] == "clarification_fallback"
