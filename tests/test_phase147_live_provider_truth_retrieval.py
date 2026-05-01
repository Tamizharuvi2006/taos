from __future__ import annotations

import pytest

from taos.apps.api.routes.agent import _resolved_answer_text, _run_entity_lookup_api_handoff
from taos.orchestration.engine import OrchestrationEngine
from taos.scripts import run_live_evidence_qa as live_qa
from taos.scripts.run_provider_connectivity_check import collect_provider_connectivity


async def _fake_linkedin_positive_web_search(*args, **kwargs):
    query = str(kwargs.get("query") or "")
    if "linkedin" in query.lower() or "founder" in query.lower() or "ceo" in query.lower():
        return {
            "success": True,
            "results": [
                {
                    "title": "Relyce Infotech LinkedIn company post",
                    "link": "https://linkedin.com/company/relyce-infotech/posts/123",
                    "snippet": "Core Team: Ukenthiran A Founder & CEO of Relyce infotech | Dharsan L | Tamizharuvi p | ...",
                },
                {
                    "title": "Unrelated Facebook CEO story",
                    "link": "https://facebook.com/unrelated-post",
                    "snippet": "US-based tech company CEO fraud discussion.",
                },
            ],
            "provider_health": {
                "serper": {"state": "closed", "last_error": "", "fallback_used": False, "cache_used": False, "failures": 0}
            },
        }
    return {"success": True, "results": [], "provider_health": {}}


async def _fake_blocked_extract(*args, **kwargs):
    return {
        "success": False,
        "error": "linkedin_blocked",
        "adapter_fallback_reason": "login_blocked",
        "attempted_adapters": ["stealth"],
    }


@pytest.mark.asyncio
async def test_blocked_linkedin_extraction_preserves_named_snippet_candidate(monkeypatch):
    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_linkedin_positive_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.extract_with_adapter", _fake_blocked_extract)

    engine = OrchestrationEngine()
    engine._settings.entity_lookup_v1_enabled = True
    result = await _run_entity_lookup_api_handoff(
        engine=engine,
        query="who is the ceo of relyce infotech",
        request_id="phase147_linkedin_blocked",
        user_id="debug_user",
        include_trace=True,
        doc_context_active=False,
    )

    summary = result.get("entity_intelligence_summary") or (result.get("trace") or {}).get("entity_intelligence_summary") or {}
    assert summary.get("selected_candidate") == "Ukenthiran A"
    assert summary.get("supported_role") == "founder_ceo"
    assert summary.get("linkedin_source_found") is True
    assert "Ukenthiran A" in _resolved_answer_text(result)


def test_provider_connectivity_shape_is_safe(monkeypatch):
    import asyncio

    async def _fake_search(*args, **kwargs):
        return {
            "results": [
                {
                    "title": "Relyce Infotech LinkedIn company post",
                    "link": "https://linkedin.com/company/relyce-infotech/posts/123",
                    "snippet": "Core Team: Ukenthiran A Founder & CEO of Relyce infotech ...",
                }
            ],
            "error": "",
        }

    async def _fake_extract(*args, **kwargs):
        return {"success": False, "error": "login_blocked", "adapter_fallback_reason": "login_blocked"}

    monkeypatch.setattr("taos.scripts.run_provider_connectivity_check.web_search", _fake_search)
    monkeypatch.setattr("taos.scripts.run_provider_connectivity_check.extract_with_adapter", _fake_extract)
    monkeypatch.setattr(
        "taos.scripts.run_provider_connectivity_check.provider_health_snapshot",
        lambda: {"serper": {"state": "closed", "last_error": ""}, "web_extract": {"state": "open", "last_error": "ConnectError"}},
    )

    report = asyncio.run(collect_provider_connectivity(base_url="http://127.0.0.1:8000", timeout=5.0))

    assert set(report.keys()) >= {
        "search_provider_ready",
        "search_provider_name",
        "extract_provider_ready",
        "external_network_ready",
        "last_provider_error_safe",
        "provider_mode",
        "live_provider_configured",
    }
    assert "API_KEY" not in str(report)


def test_live_qa_report_includes_provider_diagnostics_and_domains(monkeypatch):
    case = live_qa.LiveEvidenceCase.from_dict(
        {
            "id": "relyce_ceo_role_truth",
            "query": "who is the ceo of relyce infotech",
            "category": "entity_role",
            "expected_route": "entity_lookup",
            "expected_owner": "entity_lookup_pipeline",
            "requested_role": "ceo",
        }
    )

    monkeypatch.setattr(
        "taos.scripts.run_live_evidence_qa.collect_provider_connectivity",
        lambda **kwargs: {
            "search_provider_ready": True,
            "search_provider_name": "serper",
            "extract_provider_ready": False,
            "external_network_ready": False,
            "last_provider_error_safe": "ConnectError",
            "provider_mode": "live",
            "live_provider_configured": True,
        },
    )
    monkeypatch.setattr(
        "taos.scripts.run_live_evidence_qa._execute_live",
        lambda *args, **kwargs: {
            "answer": "The best-supported public evidence points to Ukenthiran A as founder ceo of Relyce Infotech.",
            "selected_route": "entity_lookup",
            "public_route_label": "entity_lookup",
            "route_owner": "entity_lookup_pipeline",
            "entity_intelligence_summary": {
                "selected_candidate": "Ukenthiran A",
                "requested_role": "ceo",
                "supported_role": "founder_ceo",
                "role_match": True,
                "exact_role_verified": False,
                "linkedin_source_found": True,
                "search_lanes_used": ["official_website", "linkedin"],
                "source_tiers_found": ["tier1"],
                "answer_mode": "best_supported_candidate",
                "verification_state": "candidate",
            },
            "trust_block": {
                "answer_mode": "best_supported_candidate",
                "requested_role": "ceo",
                "supported_role": "founder_ceo",
                "role_match": True,
                "exact_role_verified": False,
                "linkedin_source_found": True,
                "verification_state": "candidate",
            },
            "trace": {
                "extractor_candidates": [
                    {
                        "title": "Relyce Infotech LinkedIn company post",
                        "url": "https://linkedin.com/company/relyce-infotech/posts/123",
                        "provider": "linkedin.com",
                        "query": "Relyce Infotech Founder CEO",
                        "query_lane": "linkedin",
                        "snippet": "Core Team: Ukenthiran A Founder & CEO of Relyce infotech...",
                        "candidate_name": "Ukenthiran A",
                        "role_holder_detected": "Ukenthiran A",
                        "extracted_role": "founder_ceo",
                        "company_match": True,
                        "target_entity_match": True,
                        "role_applies_to_person": True,
                        "source_relevance_score": 0.95,
                        "extraction_status": "blocked",
                        "evidence_source": "search_result_snippet",
                    }
                ],
                "entity_search_results": [
                    {
                        "title": "Relyce Infotech LinkedIn company post",
                        "url": "https://linkedin.com/company/relyce-infotech/posts/123",
                        "provider": "linkedin.com",
                        "query": "Relyce Infotech Founder CEO",
                        "query_lane": "linkedin",
                        "snippet": "Core Team: Ukenthiran A Founder & CEO of Relyce infotech...",
                    }
                ],
            },
            "_http_status": 200,
        },
    )

    report = live_qa.run_cases([case], live=True)
    row = report["results"][0]
    assert report["provider_diagnostics"]["search_provider_name"] == "serper"
    assert "linkedin.com" in row["domains_returned"]
    assert row["provider_diagnostics"]["last_provider_error_safe"] == "ConnectError"
    assert row["evidence_matrix"][0]["role_holder_detected"] == "Ukenthiran A"
    assert row["evidence_matrix"][0]["extraction_status"] == "blocked"


def test_placeholder_candidate_is_replaced_when_snippet_has_person():
    engine = OrchestrationEngine()
    ranked = engine._rank_entity_lookup_candidates(
        rows=[
            {
                "title": "Relyce Infotech LinkedIn company post",
                "link": "https://linkedin.com/company/relyce-infotech/posts/123",
                "snippet": "Core Team: Ukenthiran A Founder & CEO of Relyce infotech | Tamizharuvi p | ...",
                "query": "site:linkedin.com/posts/relyce-infotech Founder CEO",
                "query_lane": "linkedin",
            }
        ],
        role="ceo",
        entity="Relyce Infotech",
        limit=3,
    )
    assert ranked
    assert ranked[0]["role_holder_detected"] == "Ukenthiran A"
    assert ranked[0]["company_match"] is True
