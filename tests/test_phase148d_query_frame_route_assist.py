from __future__ import annotations

from taos.core.understanding.query_canonicalizer import CanonicalizationResult
from taos.core.understanding.query_frame import QueryFrameBuilder
from taos.orchestration.engine import OrchestrationEngine


class _MockCanonicalizer:
    def __init__(self, payload: CanonicalizationResult) -> None:
        self._payload = payload

    def canonicalize(self, query: str) -> CanonicalizationResult:
        return self._payload


def _assist(engine: OrchestrationEngine, obs: dict, *, selected_route: str = "standard_task", query_kind: str = "general", doc_context_active: bool = False) -> dict:
    return engine._decide_query_frame_route_assist(
        query_frame_observation=obs,
        selected_route=selected_route,
        query_kind=query_kind,
        doc_context_active=doc_context_active,
    )


def test_flag_disabled_tamil_founder_keeps_route(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "false")
    engine = OrchestrationEngine()
    obs = engine._build_query_frame_observation(query="Microsoft யாரால் தொடங்கப்பட்டது?", selected_route="standard_task")
    decision = _assist(engine, obs, selected_route="standard_task")
    assert decision["route_assist_applied"] is False
    assert decision["route_assist_to"] == "standard_task"


def test_flag_enabled_tamil_founder_overrides_to_entity_lookup(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = engine._build_query_frame_observation(query="Microsoft யாரால் தொடங்கப்பட்டது?", selected_route="standard_task")
    decision = _assist(engine, obs, selected_route="standard_task")
    assert decision["route_assist_applied"] is True
    assert decision["route_assist_to"] == "entity_lookup"


def test_flag_enabled_hindi_ceo_overrides(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = engine._build_query_frame_observation(query="Microsoft का CEO कौन है?", selected_route="standard_task")
    decision = _assist(engine, obs, selected_route="standard_task")
    assert decision["route_assist_applied"] is True


def test_flag_enabled_spanish_founder_overrides(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = engine._build_query_frame_observation(query="¿Quién fundó Microsoft?", selected_route="standard_task")
    decision = _assist(engine, obs, selected_route="standard_task")
    assert decision["route_assist_applied"] is True


def test_arabic_founder_via_mock_semantic_overrides(monkeypatch) -> None:
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
    obs = engine._build_query_frame_observation(query="من أسس Microsoft؟", selected_route="standard_task")
    decision = _assist(engine, obs, selected_route="standard_task")
    assert decision["route_assist_applied"] is True
    assert decision["route_assist_to"] == "entity_lookup"


def test_german_ceo_via_mock_semantic_overrides(monkeypatch) -> None:
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
    obs = engine._build_query_frame_observation(query="Wer ist der CEO von Microsoft?", selected_route="standard_task")
    decision = _assist(engine, obs, selected_route="standard_task")
    assert decision["route_assist_applied"] is True


def test_block_low_confidence(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = engine._build_query_frame_observation(query="who founded microsoft", selected_route="standard_task")
    obs["confidence"] = 0.7
    decision = _assist(engine, obs)
    assert decision["route_assist_applied"] is False
    assert decision["route_assist_blocked_reason"] == "query_frame_low_confidence"


def test_block_empty_entity(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = engine._build_query_frame_observation(query="who founded microsoft", selected_route="standard_task")
    obs["entity_name"] = ""
    decision = _assist(engine, obs)
    assert decision["route_assist_blocked_reason"] == "query_frame_missing_entity"


def test_block_ambiguity(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = engine._build_query_frame_observation(query="who founded microsoft", selected_route="standard_task")
    obs["ambiguity_flags"] = ["ambiguous"]
    decision = _assist(engine, obs)
    assert decision["route_assist_blocked_reason"] == "query_frame_ambiguous"


def test_block_protected_doc_mode(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = engine._build_query_frame_observation(query="who founded microsoft", selected_route="doc_mode")
    decision = _assist(engine, obs, selected_route="doc_mode")
    assert decision["route_assist_blocked_reason"] == "protected_route"


def test_block_protected_clarification(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = engine._build_query_frame_observation(query="who founded microsoft", selected_route="clarification")
    decision = _assist(engine, obs, selected_route="clarification")
    assert decision["route_assist_blocked_reason"] == "protected_route"


def test_block_protected_deep_research(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = engine._build_query_frame_observation(query="who founded microsoft", selected_route="deep_research")
    decision = _assist(engine, obs, selected_route="deep_research")
    assert decision["route_assist_blocked_reason"] == "protected_route"


def test_block_unsupported_lookup_type(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = engine._build_query_frame_observation(query="who founded microsoft", selected_route="standard_task")
    obs["lookup_type"] = "unknown_type"
    decision = _assist(engine, obs)
    assert decision["route_assist_blocked_reason"] == "unsupported_lookup_type"
