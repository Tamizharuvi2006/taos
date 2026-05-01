from __future__ import annotations

import pytest

from taos.core.semantic.intent_classifier import ClassificationResult, DomainType, IntentType
from taos.orchestration.engine import OrchestrationEngine


def test_entity_lookup_route_label_remains_backward_compatible():
    engine = OrchestrationEngine()
    assert engine._route_label_from_selected_route("entity_lookup") == "entity_lookup"


def test_entity_lookup_query_kind_resolves_to_entity_lookup_execution_route():
    engine = OrchestrationEngine()
    engine._settings.entity_lookup_v1_enabled = True
    assert engine._resolve_selected_route(
        phase107_route="official_search",
        query_kind="entity_lookup",
    ) == "entity_lookup"


def test_entity_confirmation_gate_official_source_confirmed():
    engine = OrchestrationEngine()
    out = engine._evaluate_entity_lookup_verification(
        rows=[
            {
                "entity_role_match": True,
                "official_company_source": True,
                "company_linkedin_source": False,
                "link": "https://relyceinfotech.com/about",
            }
        ],
        role="ceo",
        entity="relyce infotech",
    )
    assert out["verification_state"] == "confirmed"
    assert out["policy_reason"] == "official_explicit_role_confirmation"
    assert out["official_source_found"] is True


def test_entity_confirmation_gate_linkedin_plus_corroboration_confirmed():
    engine = OrchestrationEngine()
    out = engine._evaluate_entity_lookup_verification(
        rows=[
            {
                "entity_role_match": True,
                "official_company_source": False,
                "company_linkedin_source": True,
                "link": "https://linkedin.com/company/relyce-infotech",
            },
            {
                "entity_role_match": True,
                "official_company_source": False,
                "company_linkedin_source": False,
                "link": "https://example.com/relyce-leadership",
            },
        ],
        role="ceo",
        entity="relyce infotech",
    )
    assert out["verification_state"] == "confirmed"
    assert out["policy_reason"] == "linkedin_plus_corroboration"


def test_entity_confirmation_gate_weak_sources_not_verified():
    engine = OrchestrationEngine()
    out = engine._evaluate_entity_lookup_verification(
        rows=[
            {
                "entity_role_match": False,
                "official_company_source": False,
                "company_linkedin_source": False,
                "link": "https://rocketreach.co/relyce",
            }
        ],
        role="ceo",
        entity="relyce infotech",
    )
    assert out["verification_state"] == "not_verified"
    assert out["policy_reason"] == "candidate_sources_without_explicit_role_confirmation"


def test_entity_confirmation_gate_employee_evidence_does_not_verify_ceo():
    engine = OrchestrationEngine()
    out = engine._evaluate_entity_lookup_verification(
        rows=[
            {
                "entity_role_match": False,
                "role_match": False,
                "supported_role": "employee",
                "role_mismatch_reason": "employee_evidence_does_not_verify_requested_role",
                "official_company_source": True,
                "company_linkedin_source": False,
                "link": "https://relyceinfotech.com/team",
            }
        ],
        role="ceo",
        entity="relyce infotech",
    )
    assert out["verification_state"] == "not_verified"
    assert out["policy_reason"] == "role_mismatch_only"
    assert out["supported_role"] == "employee"
    assert out["role_match"] is False


def test_entity_confirmation_gate_conflicting_role_evidence_stays_unverified():
    engine = OrchestrationEngine()
    out = engine._evaluate_entity_lookup_verification(
        rows=[
            {
                "entity_role_match": True,
                "role_match": True,
                "supported_role": "ceo",
                "official_company_source": False,
                "company_linkedin_source": False,
                "link": "https://example.com/leadership",
            },
            {
                "entity_role_match": False,
                "role_match": False,
                "supported_role": "employee",
                "role_mismatch_reason": "employee_evidence_does_not_verify_requested_role",
                "official_company_source": True,
                "company_linkedin_source": False,
                "link": "https://relyceinfotech.com/team",
            },
        ],
        role="ceo",
        entity="relyce infotech",
    )
    assert out["verification_state"] == "not_verified"
    assert out["policy_reason"] == "conflicting_role_evidence"
    assert out["conflict_detected"] is True


def test_entity_confirmation_gate_directory_and_profile_never_confirm():
    engine = OrchestrationEngine()
    out = engine._evaluate_entity_lookup_verification(
        rows=[
            {
                "entity_role_match": True,
                "official_company_source": False,
                "company_linkedin_source": False,
                "page_class": "directory_aggregator",
                "link": "https://rocketreach.co/relyce",
            },
            {
                "entity_role_match": True,
                "official_company_source": False,
                "company_linkedin_source": False,
                "page_class": "profile_index",
                "link": "https://linkedin.com/pub/dir/John/Doe",
            },
        ],
        role="ceo",
        entity="relyce infotech",
    )
    assert out["verification_state"] == "not_verified"


def test_entity_confirmation_gate_linkedin_and_snippet_corroboration_confirmed():
    engine = OrchestrationEngine()
    out = engine._evaluate_entity_lookup_verification(
        rows=[
            {
                "entity_role_match": True,
                "snippet_role_match": True,
                "extract_role_match": False,
                "official_company_source": False,
                "company_linkedin_source": True,
                "link": "https://linkedin.com/company/relyce-infotech",
            },
            {
                "entity_role_match": True,
                "snippet_role_match": True,
                "extract_role_match": False,
                "official_company_source": False,
                "company_linkedin_source": False,
                "link": "https://example.com/relyce-team",
            },
        ],
        role="ceo",
        entity="relyce infotech",
    )
    assert out["verification_state"] == "confirmed"
    assert out["policy_reason"] == "linkedin_plus_corroboration"


def test_entity_not_verified_trust_collapses_confidence_and_signal():
    engine = OrchestrationEngine()
    engine._trace_data = {
        "query_kind": "entity_lookup",
        "verification_state": "not_verified",
        "evidence_stats": {
            "source_count": 4,
            "provider_count": 3,
            "official_count": 0,
            "trusted_count": 1,
            "extract_count": 0,
            "extract_rejected_count": 4,
            "extraction_quality": 0.21,
            "domain_diversity": 0.66,
            "agreement_level": "low",
            "agreement_score": 0.32,
            "signal": "partial_conflict",
            "query_kind": "entity_lookup",
            "verification_state": "not_verified",
            "official_source_required": True,
            "official_source_found": False,
            "high_stakes_mode": False,
        },
    }
    trust = engine._build_trust_block(
        planner_path="entity_lookup",
        freshness={"status": "failed", "stale_phrase_detected": False, "note": None},
        fallback_used=True,
        confidence=0.72,
    )
    assert trust["confidence"] == "Low"
    assert trust["evidence"] == "Minimal"
    assert trust["agreement"] == "unknown"
    assert trust["signal"] == "candidate_only"
    assert trust["verification_state"] == "not_verified"


def test_entity_lookup_quality_mode_does_not_append_generic_research_followups():
    engine = OrchestrationEngine()
    engine._trace_data = {"goal": "who is the ceo of relyce infotech"}
    out = engine._enforce_authority_quality_blocks(
        text=(
            "Answer\n\n- Not verified.\n\n"
            "Next useful follow-ups\n"
            "- Want a timeline of events with dates and source references?\n"
            "- Want a 5-bullet concise summary of only the key points?"
        ),
        route_label="deep_research",
        intent=IntentType.RESEARCH,
        planner_path="entity_lookup",
        source_links=[],
        signal="candidate_only",
        stale_detected=False,
        conflict_detected=False,
        high_stakes_mode=False,
        official_source_found=False,
    )
    lowered = out.lower()
    assert "next useful follow-ups" not in lowered
    assert "want a timeline of events" not in lowered


def test_entity_lookup_direct_candidates_seed_urls():
    engine = OrchestrationEngine()
    rows = engine._build_entity_lookup_direct_candidates(role="ceo", entity="relyce infotech")
    links = {str(row.get("link") or "").lower() for row in rows}
    assert any("relyceinfotech.com" in link for link in links)
    assert any("linkedin.com/company/relyce-infotech" in link for link in links)


@pytest.mark.asyncio
async def test_run_entity_lookup_returns_not_verified_candidate_semantics(monkeypatch):
    async def _fake_web_search(*args, **kwargs):
        return {
            "success": True,
            "results": [
                {
                    "title": "Relyce infotech information",
                    "link": "https://rocketreach.co/relyce-infotech-profile",
                    "snippet": "Company profile listing and contact details.",
                },
                {
                    "title": "Relyce infotech company page",
                    "link": "https://in.linkedin.com/company/relyce-infotech",
                    "snippet": "Relyce infotech company page on LinkedIn.",
                },
            ],
        }

    async def _fake_extract(*args, **kwargs):
        return {
            "success": True,
            "url": kwargs.get("url", ""),
            "text": "Relyce infotech profile page with basic company details.",
            "quality_score": 0.55,
            "usable_for_research": True,
            "extractor_adapter": "web_extract",
        }

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.extract_with_adapter", _fake_extract)

    engine = OrchestrationEngine()
    engine._settings.entity_lookup_v1_enabled = True
    engine._reset_execution_trace(
        request_id="req_entity_lookup_not_verified",
        goal="can u tell me who is the ceo of relyce infotech",
        include_trace=True,
    )
    out = await engine._run_entity_lookup("can u tell me who is the ceo of relyce infotech")
    assert out is not None
    lowered = out.lower()
    assert ("best-supported candidate" in lowered) or ("not enough strong public evidence" in lowered) or ("could not verify" in lowered)
    assert "event-level" not in lowered
    assert engine._trace_data.get("entity_answer_mode") in {"best_supported_candidate", "unverified_no_answer", "role_mismatch_not_verified"}
    assert engine._trace_data.get("verification_state") in {"candidate", "not_verified"}


@pytest.mark.asyncio
async def test_finalize_entity_lookup_skips_research_synthesis_and_judge(monkeypatch):
    engine = OrchestrationEngine()
    engine._trace_data = {"planner_path": "entity_lookup"}

    classification = ClassificationResult(
        intent=IntentType.RESEARCH,
        domain=DomainType.GENERAL,
        confidence=0.8,
        metadata={"route_label": "deep_research"},
    )

    async def _fail_synthesize(*args, **kwargs):
        raise AssertionError("research synthesis should be skipped for entity_lookup terminal path")

    async def _fail_judge(*args, **kwargs):
        raise AssertionError("judge should be skipped for entity_lookup terminal path")

    monkeypatch.setattr(engine, "_synthesize_research", _fail_synthesize)
    monkeypatch.setattr(engine._judge_system, "judge_and_refine", _fail_judge)

    out = await engine._finalize(
        state=None,
        classification=classification,
        raw_result="Answer\n\n- Not verified.",
        goal_override="who is the ceo of relyce infotech",
        user_id="test_user",
    )
    assert isinstance(out, dict)
    assert out.get("formatted_response")
