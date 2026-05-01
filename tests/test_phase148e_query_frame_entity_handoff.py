from __future__ import annotations

from taos.core.understanding.query_canonicalizer import CanonicalizationResult
from taos.core.understanding.query_frame import QueryFrameBuilder
from taos.orchestration.engine import OrchestrationEngine


class _MockCanonicalizer:
    def __init__(self, payload: CanonicalizationResult) -> None:
        self._payload = payload

    def canonicalize(self, query: str) -> CanonicalizationResult:
        return self._payload


def _prepare(engine: OrchestrationEngine, query: str, selected_route: str = "entity_lookup") -> dict:
    engine._reset_execution_trace(request_id="r", goal=query, include_trace=True)
    obs = engine._build_query_frame_observation(query=query, selected_route=selected_route)
    engine._set_trace_value("query_frame", obs)
    engine._set_trace_value("route_decision", {"selected_route": selected_route})
    return obs


def test_handoff_disabled_keeps_legacy(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "false")
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    _prepare(engine, "Microsoft யாரால் தொடங்கப்பட்டது?")
    handoff = engine._build_query_frame_entity_handoff(goal="Microsoft யாரால் தொடங்கப்பட்டது?")
    assert handoff["query_frame_entity_handoff_applied"] is False
    assert handoff["query_frame_entity_handoff_blocked_reason"] == "feature_disabled"


def test_tamil_founder_handoff_enabled(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    _prepare(engine, "Microsoft யாரால் தொடங்கப்பட்டது?")
    handoff = engine._build_query_frame_entity_handoff(goal="Microsoft யாரால் தொடங்கப்பட்டது?")
    assert handoff["query_frame_entity_handoff_applied"] is True
    assert handoff["entity_handoff_entity_name"] == "Microsoft"
    assert handoff["entity_handoff_lookup_type"] == "founder_lookup"
    assert handoff["entity_handoff_requested_role"] == "founder"
    assert any("Microsoft founder" in q for q in handoff["entity_search_queries_generated"])


def test_hindi_ceo_queries(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    _prepare(engine, "Microsoft का CEO कौन है?")
    handoff = engine._build_query_frame_entity_handoff(goal="Microsoft का CEO कौन है?")
    assert handoff["query_frame_entity_handoff_applied"] is True
    assert any("Microsoft CEO" in q for q in handoff["entity_search_queries_generated"])


def test_arabic_founder_via_semantic_mock(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_SEMANTIC_CANONICALIZER_ENABLED", "true")
    engine = OrchestrationEngine()
    engine._query_frame_builder = QueryFrameBuilder(
        semantic_canonicalizer=_MockCanonicalizer(
            CanonicalizationResult(
                canonical_query="who founded Microsoft",
                intent="entity_lookup",
                lookup_type="founder_lookup",
                entity="Microsoft",
                role="founder",
                detected_language="ar",
                detected_script="Arabic",
                answer_language="ar",
                confidence=0.9,
                source="semantic_canonicalizer_mock",
            )
        )
    )
    _prepare(engine, "من أسس Microsoft؟")
    handoff = engine._build_query_frame_entity_handoff(goal="من أسس Microsoft؟")
    assert handoff["query_frame_entity_handoff_applied"] is True
    assert handoff["entity_handoff_source"] == "query_frame"


def test_german_ceo_via_semantic_mock(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_SEMANTIC_CANONICALIZER_ENABLED", "true")
    engine = OrchestrationEngine()
    engine._query_frame_builder = QueryFrameBuilder(
        semantic_canonicalizer=_MockCanonicalizer(
            CanonicalizationResult(
                canonical_query="who is the CEO of Microsoft",
                intent="entity_lookup",
                lookup_type="ceo_lookup",
                entity="Microsoft",
                role="ceo",
                detected_language="de",
                detected_script="Latin",
                answer_language="de",
                confidence=0.9,
                source="semantic_canonicalizer_mock",
            )
        )
    )
    _prepare(engine, "Wer ist der CEO von Microsoft?")
    handoff = engine._build_query_frame_entity_handoff(goal="Wer ist der CEO von Microsoft?")
    assert handoff["query_frame_entity_handoff_applied"] is True
    assert handoff["entity_handoff_requested_role"] == "ceo"


def test_tamil_linkedin_lookup_queries(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    _prepare(engine, "Relyce Infotech LinkedIn கண்டுபிடி")
    handoff = engine._build_query_frame_entity_handoff(goal="Relyce Infotech LinkedIn கண்டுபிடி")
    assert handoff["query_frame_entity_handoff_applied"] is True
    assert handoff["entity_handoff_lookup_type"] == "linkedin_profile"
    assert any("site:linkedin.com/company Relyce Infotech" == q for q in handoff["entity_search_queries_generated"])


def test_business_legitimacy_queries(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    _prepare(engine, "Relyce Infotech உண்மையான company ஆ?")
    handoff = engine._build_query_frame_entity_handoff(goal="Relyce Infotech உண்மையான company ஆ?")
    assert handoff["query_frame_entity_handoff_applied"] is True
    assert handoff["entity_handoff_lookup_type"] == "business_legitimacy"
    assert any("Relyce Infotech company profile" in q for q in handoff["entity_search_queries_generated"])


def test_block_low_confidence(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = _prepare(engine, "who founded microsoft")
    obs["confidence"] = 0.7
    engine._set_trace_value("query_frame", obs)
    handoff = engine._build_query_frame_entity_handoff(goal="who founded microsoft")
    assert handoff["query_frame_entity_handoff_blocked_reason"] == "query_frame_low_confidence"


def test_block_missing_entity(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = _prepare(engine, "who founded microsoft")
    obs["entity_name"] = ""
    engine._set_trace_value("query_frame", obs)
    handoff = engine._build_query_frame_entity_handoff(goal="who founded microsoft")
    assert handoff["query_frame_entity_handoff_blocked_reason"] == "query_frame_missing_entity"


def test_block_ambiguous(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = _prepare(engine, "who founded microsoft")
    obs["ambiguity_flags"] = ["ambiguous"]
    engine._set_trace_value("query_frame", obs)
    handoff = engine._build_query_frame_entity_handoff(goal="who founded microsoft")
    assert handoff["query_frame_entity_handoff_blocked_reason"] == "query_frame_ambiguous"


def test_block_when_route_not_entity_lookup(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    _prepare(engine, "who founded microsoft", selected_route="standard_task")
    handoff = engine._build_query_frame_entity_handoff(goal="who founded microsoft")
    assert handoff["query_frame_entity_handoff_blocked_reason"] == "route_not_entity_lookup"
