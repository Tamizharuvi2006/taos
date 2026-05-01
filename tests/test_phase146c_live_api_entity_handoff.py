from __future__ import annotations

import pytest

from taos.apps.api.response_contract import normalize_contract_payload
from taos.apps.api.routes.agent import (
    _build_agent_response_from_payload,
    _configure_engine_for_route,
    _enrich_frontend_payload,
    _infer_stream_route_hint,
    _run_entity_lookup_api_handoff,
    _resolved_answer_text,
)
from taos.orchestration.engine import OrchestrationEngine


async def _fake_entity_run() -> dict:
    async def _fake_web_search(*args, **kwargs):
        query = str(kwargs.get("query") or "")
        if "linkedin" in query.lower() or "founder & ceo" in query.lower():
            return {
                "success": True,
                "results": [
                    {
                        "title": "Relyce Infotech LinkedIn company post",
                        "link": "https://linkedin.com/company/relyce-infotech/posts/123",
                        "snippet": "Core Team: Ukenthiran A Founder & CEO of Relyce infotech | Dharsan L | Tamizharuvi p | ...",
                    }
                ],
            }
        return {"success": True, "results": []}

    async def _fake_extract(*args, **kwargs):
        return {
            "success": False,
            "error": "linkedin_blocked",
            "adapter_fallback_reason": "login_blocked",
            "attempted_adapters": ["stealth"],
        }

    return _fake_web_search, _fake_extract


@pytest.mark.asyncio
async def test_http_normalization_preserves_entity_summary_and_selected_candidate(monkeypatch):
    fake_search, fake_extract = await _fake_entity_run()
    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", fake_search)
    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.extract_with_adapter", fake_extract)

    engine = OrchestrationEngine()
    _configure_engine_for_route(engine, "entity_lookup")
    raw = await _run_entity_lookup_api_handoff(
        engine=engine,
        query="who is the ceo of relyce infotech",
        request_id="phase146c_norm",
        user_id="debug_user",
        include_trace=True,
        doc_context_active=False,
    )

    answer = _resolved_answer_text(raw)
    normalized_payload = dict(raw)
    if answer:
        normalized_payload["answer"] = answer
    normalized = normalize_contract_payload(_enrich_frontend_payload(normalized_payload))

    assert normalized["selected_route"] == "entity_lookup"
    assert normalized["public_route_label"] == "entity_lookup"
    assert normalized["entity_intelligence_summary"]["selected_candidate"] == "Ukenthiran A"
    assert normalized["entity_intelligence_summary"]["requested_role"] == "ceo"
    assert normalized["entity_intelligence_summary"]["supported_role"] == "founder_ceo"
    assert normalized["trust_block"]["selected_candidate"] == "Ukenthiran A"
    assert normalized["trust_block"]["requested_role"] == "ceo"
    assert normalized["trust_block"]["supported_role"] == "founder_ceo"
    assert normalized["trust_block"]["answer_mode"] == "best_supported_candidate"


@pytest.mark.asyncio
async def test_agent_response_trace_keeps_entity_summary(monkeypatch):
    fake_search, fake_extract = await _fake_entity_run()
    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", fake_search)
    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.extract_with_adapter", fake_extract)

    engine = OrchestrationEngine()
    _configure_engine_for_route(engine, "entity_lookup")
    raw = await _run_entity_lookup_api_handoff(
        engine=engine,
        query="who is the ceo of relyce infotech",
        request_id="phase146c_trace",
        user_id="debug_user",
        include_trace=True,
        doc_context_active=False,
    )
    answer = _resolved_answer_text(raw)
    payload = dict(raw)
    if answer:
        payload["answer"] = answer
    payload = normalize_contract_payload(_enrich_frontend_payload(payload))

    response = _build_agent_response_from_payload(
        payload,
        request_id="phase146c_trace",
        include_trace=True,
    )

    assert response.trace is not None
    assert response.trace.selected_route == "entity_lookup"
    assert response.trace.entity_intelligence_summary is not None
    assert response.trace.entity_intelligence_summary["selected_candidate"] == "Ukenthiran A"
    assert response.trust_block is not None
    assert response.trust_block.requested_role == "ceo"
    assert response.trust_block.supported_role == "founder_ceo"


@pytest.mark.asyncio
async def test_entity_route_helper_enables_v1_pipeline_for_http_path(monkeypatch):
    fake_search, fake_extract = await _fake_entity_run()
    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", fake_search)
    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.extract_with_adapter", fake_extract)

    engine = OrchestrationEngine()
    _configure_engine_for_route(engine, "entity_lookup")
    assert engine._settings.entity_lookup_v1_enabled is True

    raw = await _run_entity_lookup_api_handoff(
        engine=engine,
        query="who is the ceo of relyce infotech",
        request_id="phase146c_helper",
        user_id="debug_user",
        include_trace=True,
        doc_context_active=False,
    )

    summary = raw.get("entity_intelligence_summary") or (raw.get("trace") or {}).get("entity_intelligence_summary") or {}
    assert summary.get("selected_candidate") == "Ukenthiran A"
    assert summary.get("linkedin_source_found") is True


def test_resolved_answer_text_prefers_answer_before_result():
    assert _resolved_answer_text({"answer": "best answer", "result": "older result"}) == "best answer"


def test_stream_route_hint_uses_entity_lookup_for_profile_queries():
    assert _infer_stream_route_hint("who is the ceo of relyce infotech", False) == "entity_lookup"
    assert _infer_stream_route_hint("relyce infotech linkedin", False) == "entity_lookup"


@pytest.mark.asyncio
async def test_entity_handoff_preserves_entity_summary_without_requested_trace(monkeypatch):
    fake_search, fake_extract = await _fake_entity_run()
    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", fake_search)
    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.extract_with_adapter", fake_extract)

    engine = OrchestrationEngine()
    _configure_engine_for_route(engine, "entity_lookup")
    raw = await _run_entity_lookup_api_handoff(
        engine=engine,
        query="who is the ceo of relyce infotech",
        request_id="phase146c_no_trace",
        user_id="debug_user",
        include_trace=False,
        doc_context_active=False,
    )
    normalized = normalize_contract_payload(_enrich_frontend_payload(dict(raw)))

    assert normalized["selected_route"] == "entity_lookup"
    assert normalized["entity_intelligence_summary"]["selected_candidate"] == "Ukenthiran A"
    assert normalized["trust_block"]["selected_candidate"] == "Ukenthiran A"
