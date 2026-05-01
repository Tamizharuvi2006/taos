from __future__ import annotations

from taos.core.entity import EntityAnswerComposer, EntityEvidence, EntityIntent, EntityQuery
from taos.orchestration.engine import OrchestrationEngine


def _query(*, text: str, intent: str, entity: str, attr: str) -> EntityQuery:
    return EntityQuery(
        original_query=text,
        intent=intent,
        entity_name=entity,
        entity_type="company",
        requested_attribute=attr,
    )


def _prepare(engine: OrchestrationEngine, query: str, selected_route: str = "entity_lookup") -> dict:
    engine._reset_execution_trace(request_id="r148f", goal=query, include_trace=True)
    obs = engine._build_query_frame_observation(query=query, selected_route=selected_route)
    engine._set_trace_value("query_frame", obs)
    engine._set_trace_value("route_decision", {"selected_route": selected_route})
    return obs


def test_linkedin_profile_mode_no_role_mismatch_wording() -> None:
    answer = EntityAnswerComposer().compose(
        entity_query=_query(
            text="find LinkedIn of Relyce Infotech",
            intent=EntityIntent.LINKEDIN_PROFILE,
            entity="Relyce Infotech",
            attr="linkedin_profile",
        ),
        evidence_rows=[
            EntityEvidence(
                title="Relyce Infotech | LinkedIn",
                url="https://www.linkedin.com/company/relyce-infotech/",
                source_type="company_linkedin",
                snippet="Relyce Infotech company profile.",
                candidate_name="Relyce Infotech",
                supports_claim=True,
                company_match=True,
            )
        ],
    )
    lowered = answer.answer.lower()
    assert answer.mode == "profile_link_result"
    assert "role_mismatch" not in lowered
    assert "ceo" not in lowered
    assert "founder" not in lowered


def test_official_website_mode_no_role_mismatch_wording() -> None:
    answer = EntityAnswerComposer().compose(
        entity_query=_query(
            text="Relyce Infotech official website",
            intent=EntityIntent.COMPANY_DETAILS,
            entity="Relyce Infotech",
            attr="official_website",
        ),
        evidence_rows=[
            EntityEvidence(
                title="Relyce Infotech",
                url="https://www.relyceinfotech.com",
                source_type="official_website",
                snippet="Official company website.",
                supports_claim=True,
                company_match=True,
            )
        ],
    )
    lowered = answer.answer.lower()
    assert answer.mode == "official_website_result"
    assert "role_mismatch" not in lowered
    assert "ceo" not in lowered
    assert "founder" not in lowered


def test_business_legitimacy_mode_distinguishes_presence_vs_legal() -> None:
    answer = EntityAnswerComposer().compose(
        entity_query=_query(
            text="is Relyce Infotech a real company",
            intent=EntityIntent.LEGITIMACY_CHECK,
            entity="Relyce Infotech",
            attr="business_legitimacy",
        ),
        evidence_rows=[
            EntityEvidence(
                title="Relyce Infotech | LinkedIn",
                url="https://www.linkedin.com/company/relyce-infotech/",
                source_type="company_linkedin",
                snippet="Company profile page.",
                supports_claim=True,
            ),
            EntityEvidence(
                title="Relyce Infotech website",
                url="https://www.relyceinfotech.com",
                source_type="official_website",
                snippet="Official website candidate.",
                supports_claim=True,
            ),
        ],
    )
    lowered = answer.answer.lower()
    assert answer.mode == "business_legitimacy_result"
    assert "public online presence" in lowered
    assert "legal registration" in lowered
    assert "ceo" not in lowered
    assert "founder" not in lowered


def test_tamil_linkedin_handoff_maps_profile_link_result(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = _prepare(engine, "Relyce Infotech LinkedIn கண்டுபிடி", selected_route="entity_lookup")
    assert obs["current_selected_route"] == "entity_lookup"
    handoff = engine._build_query_frame_entity_handoff(goal="Relyce Infotech LinkedIn கண்டுபிடி")
    assert handoff["entity_handoff_lookup_type"] == "linkedin_profile"
    _ = engine._build_entity_lookup_response(
        goal="Relyce Infotech LinkedIn கண்டுபிடி",
        role_label="CEO",
        entity_label="Relyce Infotech",
        verification_state="candidate",
        policy_reason="entity_lookup_not_verified",
        evidence_rows=[
            {
                "title": "Relyce Infotech | LinkedIn",
                "link": "https://www.linkedin.com/company/relyce-infotech/",
                "snippet": "Company profile.",
                "company_linkedin_source": True,
                "company_match": True,
                "target_entity_match": True,
                "rank_score": 0.7,
            }
        ],
        queries=["Relyce Infotech LinkedIn"],
        lookup_type_hint=handoff["entity_handoff_lookup_type"],
    )
    assert engine._trace_data.get("entity_answer_mode") == "profile_link_result"


def test_tamil_legitimacy_handoff_maps_legitimacy_result(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    engine = OrchestrationEngine()
    _prepare(engine, "Relyce Infotech உண்மையான company ஆ?", selected_route="entity_lookup")
    handoff = engine._build_query_frame_entity_handoff(goal="Relyce Infotech உண்மையான company ஆ?")
    assert handoff["entity_handoff_lookup_type"] == "business_legitimacy"
    _ = engine._build_entity_lookup_response(
        goal="Relyce Infotech உண்மையான company ஆ?",
        role_label="CEO",
        entity_label="Relyce Infotech",
        verification_state="candidate",
        policy_reason="entity_lookup_not_verified",
        evidence_rows=[
            {
                "title": "Relyce Infotech website",
                "link": "https://www.relyceinfotech.com",
                "snippet": "Official website candidate.",
                "official_company_source": True,
                "company_match": True,
                "target_entity_match": True,
                "rank_score": 0.7,
            }
        ],
        queries=["Relyce Infotech official website"],
        lookup_type_hint=handoff["entity_handoff_lookup_type"],
    )
    assert engine._trace_data.get("entity_answer_mode") == "business_legitimacy_result"


def test_ceo_founder_regression_unchanged_role_paths() -> None:
    ceo_answer = EntityAnswerComposer().compose(
        entity_query=_query(
            text="who is the CEO of Relyce Infotech",
            intent=EntityIntent.CEO_LOOKUP,
            entity="Relyce Infotech",
            attr="ceo",
        ),
        evidence_rows=[
            EntityEvidence(
                title="Relyce leadership",
                url="https://example.com/leadership",
                source_type="reputable_news",
                snippet="Relyce Infotech CEO is Ukenthiran A.",
                candidate_name="Ukenthiran A",
                supported_role="ceo",
                role_match=True,
                role_holder_detected="Ukenthiran A",
                role_applies_to_person=True,
                company_match=True,
                target_entity_match=True,
                supports_claim=True,
            )
        ],
    )
    founder_answer = EntityAnswerComposer().compose(
        entity_query=_query(
            text="who founded Microsoft",
            intent=EntityIntent.FOUNDER_LOOKUP,
            entity="Microsoft",
            attr="founder",
        ),
        evidence_rows=[
            EntityEvidence(
                title="Microsoft founder history",
                url="https://example.com/history",
                source_type="reputable_news",
                snippet="Bill Gates founded Microsoft.",
                candidate_name="Bill Gates",
                supported_role="founder",
                role_match=True,
                role_holder_detected="Bill Gates",
                role_applies_to_person=True,
                company_match=True,
                target_entity_match=True,
                supports_claim=True,
            )
        ],
    )
    assert ceo_answer.mode in {"verified_entity_fact", "best_supported_candidate"}
    assert founder_answer.mode in {"verified_entity_fact", "best_supported_candidate"}


def test_non_role_no_evidence_fallback_not_role_mismatch() -> None:
    profile_answer = EntityAnswerComposer().compose(
        entity_query=_query(
            text="find LinkedIn of Relyce Infotech",
            intent=EntityIntent.LINKEDIN_PROFILE,
            entity="Relyce Infotech",
            attr="linkedin_profile",
        ),
        evidence_rows=[],
    )
    website_answer = EntityAnswerComposer().compose(
        entity_query=_query(
            text="Relyce Infotech official website",
            intent=EntityIntent.COMPANY_DETAILS,
            entity="Relyce Infotech",
            attr="official_website",
        ),
        evidence_rows=[],
    )
    legitimacy_answer = EntityAnswerComposer().compose(
        entity_query=_query(
            text="is Relyce Infotech a real company",
            intent=EntityIntent.LEGITIMACY_CHECK,
            entity="Relyce Infotech",
            attr="business_legitimacy",
        ),
        evidence_rows=[],
    )
    assert profile_answer.mode != "role_mismatch_not_verified"
    assert website_answer.mode != "role_mismatch_not_verified"
    assert legitimacy_answer.mode != "role_mismatch_not_verified"
