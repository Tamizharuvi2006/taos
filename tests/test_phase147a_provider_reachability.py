from __future__ import annotations

import asyncio

import pytest

from taos.orchestration.engine import OrchestrationEngine
from taos.scripts import run_live_evidence_qa as live_qa
from taos.scripts.run_provider_connectivity_check import collect_provider_connectivity


def test_provider_connectivity_reports_missing_api_key(monkeypatch):
    async def _fake_search(*args, **kwargs):
        return {
            "error": "Serper API key not configured",
            "error_type": "missing_api_key",
            "error_safe": "Serper API key not configured",
            "results": [],
            "search_api_key_present": False,
            "search_endpoint_configured": True,
        }

    async def _fake_extract(*args, **kwargs):
        return {"success": False, "error": "Extraction failed: ConnectError", "error_type": "ConnectError"}

    monkeypatch.setattr("taos.scripts.run_provider_connectivity_check.web_search", _fake_search)
    monkeypatch.setattr("taos.scripts.run_provider_connectivity_check.extract_with_adapter", _fake_extract)
    monkeypatch.setattr(
        "taos.scripts.run_provider_connectivity_check.provider_health_snapshot",
        lambda: {"serper": {"state": "closed", "last_error": "ConnectError"}, "web_extract": {"state": "closed", "last_error": "ConnectError"}},
    )

    report = asyncio.run(collect_provider_connectivity(base_url="http://127.0.0.1:8000", timeout=5.0))

    assert report["search_api_key_present"] is False
    assert report["search_endpoint_configured"] is True
    assert report["search_error_type"] == "missing_api_key"
    assert "mock-serper-key" not in str(report)


def test_provider_connectivity_reports_successful_search_rows(monkeypatch):
    async def _fake_search(*args, **kwargs):
        query = str(kwargs.get("query") or "")
        if "microsoft" in query.lower():
            return {
                "results": [
                    {
                        "title": "Microsoft leadership",
                        "link": "https://www.microsoft.com/en-us/about/leadership",
                        "snippet": "Satya Nadella is Chairman and Chief Executive Officer.",
                    }
                ],
                "search_api_key_present": True,
                "search_endpoint_configured": True,
                "search_http_status": 200,
            }
        return {
            "results": [
                {
                    "title": "Relyce Infotech LinkedIn company post",
                    "link": "https://linkedin.com/company/relyce-infotech/posts/123",
                    "snippet": "Core Team: Ukenthiran A Founder & CEO of Relyce infotech...",
                }
            ],
            "search_api_key_present": True,
            "search_endpoint_configured": True,
            "search_http_status": 200,
        }

    async def _fake_extract(*args, **kwargs):
        return {"success": False, "error": "login_blocked", "error_type": "blocked", "adapter_fallback_reason": "login_blocked"}

    monkeypatch.setattr("taos.scripts.run_provider_connectivity_check.web_search", _fake_search)
    monkeypatch.setattr("taos.scripts.run_provider_connectivity_check.extract_with_adapter", _fake_extract)
    monkeypatch.setattr(
        "taos.scripts.run_provider_connectivity_check.provider_health_snapshot",
        lambda: {"serper": {"state": "closed", "last_error": ""}, "web_extract": {"state": "closed", "last_error": "login_blocked"}},
    )

    report = asyncio.run(collect_provider_connectivity(base_url="http://127.0.0.1:8000", timeout=5.0))

    assert report["search_results_count"] == 1
    assert "linkedin.com" in report["search_domains"]
    assert report["search_known_results_count"] == 1
    assert "microsoft.com" in report["search_known_domains"]
    assert report["extract_error_type"] == "blocked"


def test_live_qa_marks_provider_connectivity_failure_not_no_evidence(monkeypatch):
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
            "search_provider_ready": False,
            "search_provider_name": "serper",
            "search_api_key_present": True,
            "search_endpoint_configured": True,
            "search_http_status": None,
            "search_error_type": "ConnectError",
            "search_error_safe": "web_search_failed: ConnectError",
            "extract_provider_ready": False,
            "extract_error_type": "ConnectError",
            "extract_error_safe": "Extraction failed: All connection attempts failed",
            "external_network_ready": False,
            "dns_resolution_ok": False,
            "proxy_detected": False,
            "last_provider_error_safe": "web_search_failed: ConnectError",
            "provider_mode": "live",
            "live_provider_configured": True,
            "provider_connectivity_failed": True,
        },
    )
    monkeypatch.setattr(
        "taos.scripts.run_live_evidence_qa._execute_live",
        lambda *args, **kwargs: {
            "answer": "I could not verify ceo for Relyce Infotech from public evidence.\n\nProvider availability:\n- Live search or extraction provider was unavailable in this run.",
            "selected_route": "entity_lookup",
            "route_owner": "entity_lookup_pipeline",
            "entity_intelligence_summary": {
                "requested_role": "ceo",
                "supported_role": "",
                "answer_mode": "unverified_no_answer",
                "verification_state": "not_verified",
                "search_lanes_used": ["official_website", "linkedin"],
                "source_tiers_found": ["tier1"],
                "provider_connectivity_failed": True,
                "provider_error_safe": "web_search_failed: ConnectError",
                "source_plan": {"lanes": {"linkedin": ["Relyce Infotech Founder CEO"]}, "required_lanes": ["official_website", "linkedin"]},
            },
            "trust_block": {
                "answer_mode": "unverified_no_answer",
                "requested_role": "ceo",
                "supported_role": "",
                "confidence_reason": "Provider connectivity failed before usable evidence was gathered.",
            },
            "trace": {
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
                "extractor_candidates": [
                    {
                        "title": "Relyce Infotech LinkedIn company post",
                        "url": "https://linkedin.com/company/relyce-infotech/posts/123",
                        "attempted": True,
                        "extraction_status": "blocked",
                        "rejection_reason": "",
                    }
                ],
                "provider_health": {
                    "serper": {"state": "closed", "last_error": "ConnectError"},
                    "web_extract": {"state": "closed", "last_error": "ConnectError"},
                },
            },
            "_http_status": 200,
        },
    )

    report = live_qa.run_cases([case], live=True)
    row = report["results"][0]

    assert row["provider_diagnostics"]["provider_connectivity_failed"] is True
    assert row["search_queries_generated"] == ["Relyce Infotech Founder CEO"]
    assert row["provider_returned_count"] == 1
    assert "linkedin.com" in row["domains_returned"]
    assert row["returned_titles"] == ["Relyce Infotech LinkedIn company post"]
    assert row["extracted_urls_attempted"] == ["https://linkedin.com/company/relyce-infotech/posts/123"]
    assert row["extraction_statuses"] == ["blocked"]
    assert "unavailable-provider" in row["pass_reason"].lower() or "provider connectivity failed" in row["pass_reason"].lower()


@pytest.mark.asyncio
async def test_entity_answer_mentions_provider_unavailable_when_connectivity_fails():
    engine = OrchestrationEngine()
    text = engine._build_entity_lookup_response(
        goal="who is the ceo of relyce infotech",
        role_label="CEO",
        entity_label="Relyce Infotech",
        verification_state="not_verified",
        policy_reason="provider_connectivity_failed",
        evidence_rows=[],
        queries=["Relyce Infotech Founder CEO"],
        provider_connectivity_failed=True,
        provider_error_safe="web_search_failed: ConnectError",
    )
    assert "Provider availability:" in text
    assert "ConnectError" in text
