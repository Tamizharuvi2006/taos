from __future__ import annotations

from types import SimpleNamespace

from taos.core.semantic.query_rewriter import QueryRewriter
from taos.core.semantic.query_normalizer import normalize_user_query
from taos.core.semantic.intent_classifier import DomainType, IntentType
from taos.core.semantic.interpretation import RequestInterpreter
from taos.core.entity import EntityAnswerComposer, EntityEvidence, EntityIntentDetector
from taos.core.entity.entity_resolver import PublicEntityResolver
from taos.core.routing.route_rules import deterministic_route
from taos.core.understanding.intent_frame import IntentFrame
from taos.core.understanding.route_hint_builder import RouteHintBuilder
from taos.orchestration.engine import OrchestrationEngine
from taos.scripts.run_live_evidence_qa import extract_report_fields


def test_research_search_query_preserves_real_serper_rows():
    async def _fake_web_search(**kwargs):
        return {
            "results": [
                {
                    "title": "Microsoft Leadership",
                    "link": "https://www.microsoft.com/en-us/about",
                    "snippet": "Satya Nadella is Chairman and Chief Executive Officer.",
                    "position": 1,
                }
            ]
        }

    engine = OrchestrationEngine()
    result = __import__("asyncio").run(
        engine._run_research_search_query(
            query_text="who is the ceo of microsoft",
            freshness_mode="current",
            search_type="search",
            recency_days=None,
            web_search_fn=_fake_web_search,
            cache_summary=engine._new_research_cache_summary(),
        )
    )

    assert len(result["rows"]) == 1
    assert result["rows"][0]["title"] == "Microsoft Leadership"
    assert "microsoft.com" in result["rows"][0]["provider"]
    assert result["rows"][0]["query"] == "who is the ceo of microsoft"


def test_relyce_linkedin_snippet_extracts_ukenthiran_as_founder_ceo():
    engine = OrchestrationEngine()
    ranked = engine._rank_entity_lookup_candidates(
        rows=[
            {
                "title": "Relyce Infotech LinkedIn company post",
                "link": "https://linkedin.com/company/relyce-infotech/posts/123",
                "snippet": "Core Team : Ukenthiran A Founder & CEO of Relyce infotech Dharsan L | Tamizharuvi p ...",
                "query": "Relyce Infotech Founder CEO",
                "query_lane": "linkedin",
            }
        ],
        role="ceo",
        entity="Relyce Infotech",
    )

    assert ranked
    assert ranked[0]["role_holder_detected"] == "Ukenthiran A"
    assert ranked[0]["supported_role"] == "founder_ceo"
    assert ranked[0]["query_lane"] == "linkedin"


def test_entity_profile_prompts_route_to_entity_lookup():
    queries = [
        "relyce infotech official website",
        "find linkedin of relyce infotech",
        "is relyce infotech real company",
    ]
    for query in queries:
        decision = deterministic_route(query.lower(), {})
        assert decision is not None
        assert decision.route == "entity_lookup"


def test_query_rewriter_does_not_wrap_entity_profile_prompts_as_generic_research():
    rewriter = QueryRewriter()
    result = rewriter.rewrite(
        "relyce infotech official website",
        intent=IntentType.RESEARCH,
        domain=DomainType.GENERAL,
    )
    assert result.rewritten == "relyce infotech official website"


def test_route_hint_builder_routes_entity_profile_intents_to_entity_lookup():
    frame = IntentFrame(
        original_query="relyce infotech official website",
        cleaned_query="relyce infotech official website",
        intent="entity_lookup",
        entity_intelligence_summary={"intent": "company_details", "entity_name": "Relyce Infotech"},
    )
    route, reason, _ = RouteHintBuilder().build(frame)
    assert route == "entity_lookup"
    assert "entity" in reason


def test_request_interpreter_marks_official_site_as_entity_lookup_query_kind():
    interpreter = RequestInterpreter()
    normalized = interpreter.normalize_query("relyce infotech official website")
    classification = SimpleNamespace(
        intent=IntentType.RESEARCH,
        domain=DomainType.GENERAL,
        metadata={"route_label": "entity_lookup"},
        is_followup=False,
        confidence=0.9,
    )
    envelope = interpreter.build_envelope(
        raw_query="relyce infotech official website",
        classification=classification,
        rewrite=type("R", (), {"rewritten": normalized, "was_modified": False, "reason": ""})(),
        has_context=False,
        has_active_doc=False,
    )
    assert envelope["routing_profile"]["query_kind"] == "entity_lookup"


def test_who_founded_microsoft_parses_entity_and_role_cleanly():
    detected = EntityIntentDetector().detect("who founded microsoft")
    assert detected.entity_name == "Microsoft"
    assert detected.requested_attribute == "founder"

    role, entity = OrchestrationEngine()._extract_profile_role_and_entity("who founded microsoft")
    assert role == "founder"
    assert entity == "microsoft"

    resolved = PublicEntityResolver().resolve("who founded microsoft")
    assert resolved["entity_name"] == "Microsoft"

    wrapped = PublicEntityResolver().resolve("Research and verify with current sources: who founded microsoft")
    assert wrapped["entity_name"] == "Microsoft"

    prefixed_role, prefixed_entity = OrchestrationEngine()._extract_profile_role_and_entity(
        "Research and verify with current sources: who founded microsoft"
    )
    assert prefixed_role == "founder"
    assert prefixed_entity == "microsoft"


def test_query_normalizer_strips_only_instruction_wrappers():
    assert (
        normalize_user_query("Research and verify with current sources: who is the CEO of Relyce Infotech?")
        == "who is the CEO of Relyce Infotech?"
    )
    assert (
        normalize_user_query("Comprehensive research and verify with current sources: Relyce Infotech founder")
        == "Relyce Infotech founder"
    )
    assert normalize_user_query("Comprehensive Insurance CEO") == "Comprehensive Insurance CEO"
    assert normalize_user_query("Detailed Solutions Pvt Ltd founder") == "Detailed Solutions Pvt Ltd founder"


def test_public_entity_resolver_does_not_damage_real_company_names():
    resolved = PublicEntityResolver().resolve("Comprehensive Insurance company CEO")
    assert resolved["entity_name"] == "Comprehensive Insurance"

    resolved_detailed = PublicEntityResolver().resolve("Detailed Solutions Pvt Ltd founder")
    assert resolved_detailed["entity_name"] == "Detailed Solutions PVT Ltd"


def test_microsoft_ceo_snippet_selects_satya_nadella():
    query = EntityIntentDetector().detect("who is the ceo of microsoft")
    answer = EntityAnswerComposer().compose(
        entity_query=query,
        evidence_rows=[
            EntityEvidence(
                title="Microsoft leadership",
                url="https://www.microsoft.com/en-us/about",
                source_type="official_website",
                snippet="Satya Nadella is Chairman and Chief Executive Officer of Microsoft.",
                candidate_name="Satya Nadella",
                requested_role="ceo",
                supported_role="ceo",
                role_match=True,
                source_tier="tier1",
                extraction_quality=0.8,
                usable_for_verification=True,
                supports_claim=True,
                company_match=True,
                target_entity_match=True,
                role_holder_detected="Satya Nadella",
                extracted_role="ceo",
                role_applies_to_person=True,
                source_relevance_score=0.95,
            )
        ],
    )
    assert answer.selected_candidate == "Satya Nadella"
    assert answer.verification_state == "confirmed"


def test_hyphenated_microsoft_ceo_title_extracts_satya_nadella():
    engine = OrchestrationEngine()
    ranked = engine._rank_entity_lookup_candidates(
        rows=[
            {
                "title": "Satya Nadella - Chairman and CEO at Microsoft - LinkedIn",
                "link": "https://www.linkedin.com/in/satyanadella",
                "snippet": "As chairman and CEO of Microsoft, Satya Nadella leads the company.",
                "query": "who is the ceo of microsoft",
                "query_lane": "linkedin",
            }
        ],
        role="ceo",
        entity="Microsoft",
    )

    assert ranked
    assert ranked[0]["role_holder_detected"] == "Satya Nadella"


def test_role_holder_extractor_ignores_negated_claims():
    engine = OrchestrationEngine()
    detected = engine._extract_role_holder_from_text(
        text="People incorrectly claim John is CEO, but he is not.",
        role="ceo",
    )
    assert detected == ""


def test_live_qa_parser_counts_provider_rows_when_serper_works():
    payload = {
        "answer": "The best-supported public evidence points to Ukenthiran A as Founder & CEO of Relyce Infotech.",
        "selected_route": "entity_lookup",
        "route_owner": "entity_lookup_pipeline",
        "entity_intelligence_summary": {
            "answer_mode": "best_supported_candidate",
            "requested_role": "ceo",
            "supported_role": "founder_ceo",
            "selected_candidate": "Ukenthiran A",
            "search_lanes_used": ["official_website", "linkedin"],
            "source_tiers_found": ["tier1"],
            "verification_state": "candidate",
        },
        "trust_block": {
            "answer_mode": "best_supported_candidate",
            "requested_role": "ceo",
            "supported_role": "founder_ceo",
            "role_match": True,
        },
        "trace": {
            "entity_search_results": [
                {
                    "title": "Relyce Infotech LinkedIn company post",
                    "url": "https://linkedin.com/company/relyce-infotech/posts/123",
                    "provider": "linkedin.com",
                    "query": "Relyce Infotech Founder CEO",
                    "query_lane": "linkedin",
                    "snippet": "Core Team : Ukenthiran A Founder & CEO of Relyce infotech ...",
                }
            ],
            "extractor_candidates": [
                {
                    "title": "Relyce Infotech LinkedIn company post",
                    "url": "https://linkedin.com/company/relyce-infotech/posts/123",
                    "provider": "linkedin.com",
                    "query_lane": "linkedin",
                    "snippet": "Core Team : Ukenthiran A Founder & CEO of Relyce infotech ...",
                    "company_match": True,
                    "target_entity_match": True,
                    "role_holder_detected": "Ukenthiran A",
                    "extracted_role": "founder_ceo",
                    "role_applies_to_person": True,
                    "source_relevance_score": 0.92,
                    "quality_score": 0.6,
                    "extraction_status": "blocked",
                    "evidence_source": "search_result_snippet",
                    "attempted": True,
                }
            ],
            "provider_health": {"serper": {"state": "closed", "last_error": ""}, "web_extract": {"state": "closed", "last_error": ""}},
        },
        "provider_diagnostics": {
            "search_provider_ready": True,
            "search_http_status": 200,
            "search_api_key_present": True,
            "search_endpoint_configured": True,
            "extract_provider_ready": True,
            "external_network_ready": True,
        },
        "_http_status": 200,
    }

    fields = extract_report_fields(payload)

    assert fields["provider_returned_count"] == 1
    assert "linkedin.com" in fields["domains_returned"]
    assert fields["returned_titles"] == ["Relyce Infotech LinkedIn company post"]
    assert fields["trust_metadata"]["selected_candidate"] == "Ukenthiran A"
