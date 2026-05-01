from __future__ import annotations

from taos.apps.api.schemas.agent import AgentResponse
from taos.apps.api.schemas.trace import TraceResponse
from taos.orchestration.engine import OrchestrationEngine


def _build_trace_payload() -> dict:
    return {
        "request_id": "req_123",
        "intent": "research",
        "mode": "deep",
        "route_label": "deep_research",
        "route_source": "interpreter_v1",
        "route_confidence": 0.93,
        "interpretation": {
            "raw_query": "older one what done",
            "normalized_query": "older one what done",
            "rewritten_query": "Summarize the previously discussed item.",
        },
        "routing_profile": {
            "primary_intent": "follow_up",
            "ambiguity_score": 0.62,
            "context_required": True,
            "grounding_need": "memory",
            "risk_level": "low",
        },
        "route_decision": {
            "selected_route": "clarification",
            "route_reason": "ambiguous_followup_without_context",
            "verification_status": "passed",
            "rerouted": False,
        },
        "planning_handoff": {
            "raw_query": "older one what done",
            "rewritten_query": "Summarize the previously discussed item.",
            "context_required": True,
        },
        "doc_context_active": True,
        "planner_path": "dag_exec",
        "dag_name": "research_v2",
        "pipeline_stages": ["Query", "Search", "Rank", "Extract", "Synthesize", "Trust"],
        "tone_profile": {
            "current": "serious",
            "blended": "serious",
            "serious": 0.71,
            "casual": 0.21,
            "playful": 0.08,
            "emoji_allowed": False,
            "banter_allowed": False,
            "style_hint": "Use professional concise tone. Avoid decorative emojis.",
            "thresholds": {
                "serious_override_threshold": 0.65,
                "emoji_allowed_serious_max": 0.58,
                "banter_allowed_playful_min": 0.56,
                "blend_current_weight": 0.78,
                "blend_prior_weight": 0.22,
                "decay_min_factor": 0.12,
            },
        },
        "fsm_transitions": [
            "INIT -> PLANNING",
            "PLANNING -> PLAN_READY",
            "PLAN_READY -> EXECUTING",
            "EXECUTING -> REFLECTING",
            "REFLECTING -> TERMINATING",
        ],
        "steps": [
            {
                "id": "step_1",
                "type": "dag_exec",
                "status": "success",
                "tool": "dag:research_v2",
                "retries": 0,
                "latency_ms": 182.4,
                "summary": "Completed DAG step with 3 node(s).",
                "error": None,
                "execution_mode": "parallel",
                "frontier_count": 2,
                "batches": [
                    {
                        "batch_index": 1,
                        "frontier_index": 1,
                        "status": "success",
                        "node_ids": ["search_latest", "search_context"],
                        "latency_ms": 61.2,
                    }
                ],
                "nodes": [
                    {
                        "id": "search_latest",
                        "type": "tool",
                        "status": "success",
                        "tool": "web_search",
                        "retries": 0,
                        "latency_ms": 61.2,
                        "summary": "Collected 5 result(s).",
                        "error": None,
                        "batch_index": 1,
                        "frontier_index": 1,
                    }
                ],
            }
        ],
        "fallback_used": False,
        "fallback_reason": None,
        "freshness_check": {
            "status": "passed",
            "stale_phrase_detected": False,
            "note": None,
        },
        "confidence": 0.91,
        "trust_block": {
            "freshness": "High",
            "evidence": "Strong",
            "execution_path": "Structured Research",
            "fallback_used": False,
            "confidence": "High",
            "source_count": 4,
            "usable_sources_count": 3,
            "rejected_sources_count": 1,
            "last_verified": "2026-04-07",
            "agreement": "medium",
            "agreement_score": 0.62,
            "signal": "partial_conflict",
            "domain_diversity": 0.75,
            "extraction_quality": 0.81,
            "official_source_required": True,
            "official_source_found": True,
            "uncertainty_flags": ["partial_conflict"],
        },
    }


def test_trace_response_accepts_public_execution_trace_shape():
    trace = TraceResponse.model_validate(_build_trace_payload())

    assert trace.request_id == "req_123"
    assert trace.intent == "research"
    assert trace.mode == "deep"
    assert trace.route_label == "deep_research"
    assert trace.route_source == "interpreter_v1"
    assert trace.route_confidence == 0.93
    assert trace.interpretation is not None
    assert trace.routing_profile is not None
    assert trace.route_decision is not None
    assert trace.planning_handoff is not None
    assert trace.doc_context_active is True
    assert trace.planner_path == "dag_exec"
    assert trace.dag_name == "research_v2"
    assert trace.pipeline_stages == ["Query", "Search", "Rank", "Extract", "Synthesize", "Trust"]
    assert trace.fallback_used is False
    assert trace.freshness_check.status == "passed"
    assert len(trace.fsm_transitions) == 5
    assert len(trace.steps) == 1
    assert trace.steps[0].nodes[0].tool == "web_search"
    assert trace.steps[0].execution_mode == "parallel"
    assert trace.trust_block is not None
    assert trace.trust_block.execution_path == "Structured Research"
    assert trace.trust_block.extraction_quality == 0.81
    assert trace.trust_block.usable_sources_count == 3
    assert trace.trust_block.official_source_required is True
    assert trace.tone_profile is not None
    assert trace.tone_profile.current == "serious"
    assert trace.tone_profile.emoji_allowed is False
    assert trace.tone_profile.thresholds is not None
    assert trace.tone_profile.thresholds.serious_override_threshold == 0.65


def test_agent_response_trace_is_optional():
    response = AgentResponse.model_validate(
        {
            "answer": "The product of 25 and 17 is 425.",
            "direct_answer": "425",
            "intent": "simple_lookup",
            "domain": "general",
            "mode": "fast",
            "confidence": 0.99,
            "fast_path": True,
            "steps_executed": 0,
            "cost": 0.0,
            "elapsed_ms": 12.0,
            "request_id": "req_123",
            "replan_count": 0,
        }
    )

    assert response.answer == "The product of 25 and 17 is 425."
    assert response.trace is None


def test_agent_response_embeds_trace_when_requested():
    response = AgentResponse.model_validate(
        {
            "answer": "As of April 8, 2026, gold is trading near record highs.",
            "direct_answer": "Gold is near record highs.",
            "key_points": ["Latest price came from live sources."],
            "intent": "research",
            "domain": "general",
            "mode": "deep",
            "confidence": 0.91,
            "sources": [],
            "fast_path": False,
            "steps_executed": 1,
            "cost": 0.02,
            "elapsed_ms": 820.0,
            "request_id": "req_123",
            "replan_count": 0,
            "trace": _build_trace_payload(),
        }
    )

    assert response.trace is not None
    assert response.trace.planner_path == "dag_exec"
    assert response.trace.steps[0].type == "dag_exec"


def test_verified_package_version_fast_search_gets_high_trust():
    engine = OrchestrationEngine()
    engine._trace_data = {
        "planner_path": "fast_search",
        "evidence_stats": {
            "source_count": 1,
            "provider_count": 1,
            "official_count": 1,
            "domain_diversity": 1.0,
            "source_diversity_score": 1.0,
            "extraction_quality": 0.0,
            "query_kind": "version_lookup",
            "verification_state": "verified",
            "official_source_required": True,
            "official_source_found": True,
            "signal": "clean",
            "source_rows": [
                {
                    "title": "vite - npm",
                    "link": "https://www.npmjs.com/package/vite",
                    "provider": "npmjs.com",
                    "domain": "npmjs.com",
                    "tier": "official",
                    "source_of_record": True,
                    "source_type": "package_registry",
                    "snippet": "npm registry latest tag for vite is 99.0.0.",
                }
            ],
        },
    }

    report = engine._build_evidence_report(
        answer_text="The latest version I could verify for Vite is 99.0.0. [S1]"
    )
    trust = engine._build_trust_block(
        planner_path="fast_search",
        freshness={"status": "passed"},
        fallback_used=False,
        confidence=0.92,
        evidence_report=report,
    )

    assert trust["confidence"] == "High"
    assert trust["evidence"] == "Strong"
    assert trust["citation_coverage"] == 1.0
    assert trust["unsupported_claims"] == 0
    assert trust["official_source_found"] is True
    assert trust["confidence_reason"] == "Version confirmed from the npm registry latest tag."
