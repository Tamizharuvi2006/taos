from __future__ import annotations

import pytest

from taos.orchestration.engine import OrchestrationEngine
from taos.scripts import run_live_evidence_qa as phase146


@pytest.mark.asyncio
async def test_entity_lookup_uses_phase146a_queries_in_live_path(monkeypatch):
    seen_queries: list[str] = []

    async def _fake_web_search(*args, **kwargs):
        seen_queries.append(str(kwargs.get("query") or ""))
        return {"success": True, "results": []}

    async def _fake_extract(*args, **kwargs):
        return {"success": False, "error": "blocked"}

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.extract_with_adapter", _fake_extract)

    engine = OrchestrationEngine()
    engine._settings.entity_lookup_v1_enabled = True
    engine._reset_execution_trace("req_146b_queries", "who is the ceo of relyce infotech", include_trace=True)
    await engine._run_entity_lookup("who is the ceo of relyce infotech")

    assert any("site:linkedin.com/posts/relyce-infotech founder & ceo" in q.lower() for q in seen_queries)
    assert any("linkedin founder & ceo" in q.lower() for q in seen_queries)


@pytest.mark.asyncio
async def test_linkedin_company_result_is_retained_when_extraction_is_blocked(monkeypatch):
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
                    },
                    {
                        "title": "Unrelated Facebook CEO story",
                        "link": "https://facebook.com/unrelated-post",
                        "snippet": "US-based tech company CEO fraud discussion.",
                    },
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

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.extract_with_adapter", _fake_extract)

    engine = OrchestrationEngine()
    engine._settings.entity_lookup_v1_enabled = True
    engine._reset_execution_trace("req_146b_linkedin", "who is the ceo of relyce infotech", include_trace=True)
    out = await engine._run_entity_lookup("who is the ceo of relyce infotech")

    lowered = str(out or "").lower()
    assert "ukenthiran a" in lowered
    assert "linkedin-supported candidate evidence" in lowered
    assert "aaron levie" not in lowered
    assert engine._trace_data.get("requested_role") == "ceo"
    summary = engine._trace_data.get("entity_intelligence_summary") or {}
    assert summary.get("selected_candidate") == "Ukenthiran A"
    assert summary.get("linkedin_source_found") is True
    assert summary.get("requested_role") == "ceo"


def test_unrelated_facebook_candidate_is_rejected_by_live_ranker():
    engine = OrchestrationEngine()
    ranked = engine._rank_entity_lookup_candidates(
        rows=[
            {
                "title": "Unrelated Facebook CEO story",
                "link": "https://facebook.com/unrelated-post",
                "snippet": "US-based tech company CEO fraud discussion.",
                "query": "relyce infotech CEO",
                "query_lane": "general_web",
            },
            {
                "title": "Relyce Infotech LinkedIn company post",
                "link": "https://linkedin.com/company/relyce-infotech/posts/123",
                "snippet": "Core Team: Ukenthiran A Founder & CEO of Relyce infotech | Dharsan L | Tamizharuvi p | ...",
                "query": "site:linkedin.com/posts/relyce-infotech Founder & CEO",
                "query_lane": "linkedin",
            },
        ],
        role="ceo",
        entity="Relyce Infotech",
        limit=5,
    )

    assert ranked[0]["link"].startswith("https://linkedin.com/company/relyce-infotech")
    assert all("facebook.com/unrelated-post" not in str(row.get("link") or "") for row in ranked)


def test_live_qa_report_exposes_generated_queries_and_rejected_sources():
    case = phase146.LiveEvidenceCase.from_dict(
        {
            "id": "relyce_ceo_role_truth",
            "query": "who is the ceo of relyce infotech",
            "category": "entity_role",
            "expected_route": "entity_lookup",
            "expected_owner": "entity_lookup_pipeline",
            "requested_role": "ceo",
            "expect_not_verified": True,
            "forbid_candidate_name": "Tamizh Aruvi",
            "require_search_lanes": ["official_website", "linkedin"],
            "allow_supported_roles": ["employee", ""],
        }
    )
    report = phase146.run_cases([case], live=False)
    row = report["results"][0]
    assert row["search_queries_generated"]
    assert row["trust_metadata"]["requested_role"] == "ceo"
    assert any(item.get("rejection_reason") == "tier3_snippet_only" for item in row["evidence_matrix"])
