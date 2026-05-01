from __future__ import annotations

from taos.apps.api.response_contract import build_clarification_payload, build_timeout_payload, normalize_contract_payload
from taos.apps.api.routes.agent import _build_agent_response_from_payload, _build_micro_fast_payload, _enrich_frontend_payload
from taos.apps.api.schemas.agent import AgentResponse
from taos.core.streaming.events import StreamEvent, StreamEventType


def _assert_unified_fields(payload: dict) -> None:
    required = {
        "answer",
        "direct_answer",
        "sections",
        "key_points",
        "confidence",
        "trust_block",
        "sources",
        "route",
        "mode",
        "warnings",
        "metadata",
    }
    missing = [field for field in required if field not in payload]
    assert not missing, f"Missing unified fields: {missing}"


def test_execute_contract_shape_on_fast_payload():
    payload = normalize_contract_payload(
        _enrich_frontend_payload(
            _build_micro_fast_payload(
                answer="Hey. I am here and ready.",
                request_id="req_fast",
                doc_context_active=False,
                elapsed_ms=12.0,
                include_trace=True,
            )
        )
    )
    _assert_unified_fields(payload)
    response = _build_agent_response_from_payload(payload, request_id="req_fast", include_trace=True)
    assert isinstance(response, AgentResponse)
    assert response.route == "fast_message"
    assert response.sections == response.answer_sections
    assert response.trace is not None


def test_execute_stream_final_contract_shape_matches_normal():
    final_payload = normalize_contract_payload(
        _enrich_frontend_payload(
            {
                "answer": "Research answer",
                "direct_answer": "Research answer",
                "key_points": ["One"],
                "intent": "research",
                "domain": "general",
                "mode": "deep",
                "confidence": 0.61,
                "sources": ["https://example.com/a"],
                "route": "deep_research",
                "route_label": "deep_research",
                "trust_block": {
                    "freshness": "Good",
                    "evidence": "Moderate",
                    "execution_path": "Structured Research",
                    "fallback_used": False,
                    "confidence": "Medium",
                    "source_count": 1,
                    "usable_sources_count": 1,
                    "rejected_sources_count": 0,
                    "official_source_count": 0,
                    "agreement": "medium",
                    "agreement_score": 0.58,
                    "conflict_detected": False,
                    "stale_detected": False,
                    "signal": "clean",
                    "citation_coverage": 0.78,
                    "supported_claims": 5,
                    "partially_supported_claims": 1,
                    "unsupported_claims": 0,
                    "overall_support": "supported",
                    "confidence_reason": "Confidence adjusted from evidence coverage 78%",
                    "evidence_matrix_summary": {
                        "claim_count": 6,
                        "supported_claims": 5,
                        "partially_supported_claims": 1,
                        "unsupported_claims": 0,
                        "citation_coverage": 0.78,
                    },
                    "uncertainty_flags": [],
                },
                "evidence_matrix_summary": {
                    "claim_count": 6,
                    "supported_claims": 5,
                    "partially_supported_claims": 1,
                    "unsupported_claims": 0,
                    "citation_coverage": 0.78,
                },
                "trace": {"request_id": "req_stream", "route_label": "deep_research", "confidence": 0.61},
            }
        )
    )
    evt = StreamEvent(
        request_id="req_stream",
        event_type=StreamEventType.FINAL,
        phase_name="complete",
        progress=100,
        message="Execution complete",
        payload=final_payload,
    )
    event_payload = evt.to_sse_payload()["payload"]
    _assert_unified_fields(event_payload)
    assert "evidence_matrix_summary" in event_payload


def test_timeout_fallback_returns_unified_contract():
    payload = normalize_contract_payload(build_timeout_payload(request_id="req_timeout", elapsed_ms=50.0, partial_result="Partial"))
    _assert_unified_fields(payload)
    assert payload["route"] == "standard_task"
    assert payload["answer_sections"] == payload["sections"]


def test_clarification_fallback_returns_unified_contract():
    payload = normalize_contract_payload(build_clarification_payload(request_id="req_clarify", query="this one"))
    _assert_unified_fields(payload)
    assert payload["route"] == "standard_task"
    assert payload["metadata"]["fallback_reason"] == "clarification"


def test_research_response_includes_evidence_matrix_summary_and_warning_chips():
    payload = normalize_contract_payload(
        _enrich_frontend_payload(
            {
                "answer": "Weakly supported research answer.",
                "direct_answer": "Weakly supported research answer.",
                "intent": "research",
                "domain": "general",
                "mode": "deep",
                "confidence": 0.42,
                "sources": ["https://example.com/a"],
                "route_label": "deep_research",
                "trust_block": {
                    "freshness": "Medium",
                    "evidence": "Weak",
                    "execution_path": "Structured Research",
                    "fallback_used": False,
                    "confidence": "Low",
                    "source_count": 1,
                    "usable_sources_count": 1,
                    "rejected_sources_count": 0,
                    "official_source_count": 0,
                    "agreement": "low",
                    "agreement_score": 0.21,
                    "conflict_detected": True,
                    "stale_detected": True,
                    "signal": "conflicting",
                    "citation_coverage": 0.33,
                    "supported_claims": 1,
                    "partially_supported_claims": 0,
                    "unsupported_claims": 2,
                    "overall_support": "weak",
                    "confidence_reason": "Confidence adjusted from evidence coverage 33%",
                    "evidence_matrix_summary": {
                        "claim_count": 3,
                        "supported_claims": 1,
                        "partially_supported_claims": 0,
                        "unsupported_claims": 2,
                        "citation_coverage": 0.33,
                    },
                    "uncertainty_flags": ["unsupported_claims", "stale_evidence", "conflicting_evidence"],
                },
            }
        )
    )
    assert payload["evidence_matrix_summary"]["unsupported_claims"] == 2
    badge_keys = {badge["key"] for badge in payload["trust_badges"]}
    assert "coverage" in badge_keys
    assert "unsupported_claims" in badge_keys
    assert any("confidence was reduced automatically" in warning.lower() for warning in payload["warnings"])


def test_old_compatibility_fields_still_exist():
    response = AgentResponse.model_validate(
        normalize_contract_payload(
            {
                "answer": "Compat",
                "direct_answer": "Compat",
                "answer_sections": [{"key": "answer", "title": "Answer", "content": "Compat", "bullets": []}],
                "route_label": "standard_task",
                "mode": "standard",
                "confidence": 0.5,
                "sources": [],
                "trust_block": None,
            }
        )
    )
    assert response.answer_sections == response.sections
    assert response.route == "standard_task"
