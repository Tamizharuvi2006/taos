from __future__ import annotations

from taos.orchestration.engine import OrchestrationEngine


def _prepare(engine: OrchestrationEngine, query: str, selected_route: str = "entity_lookup") -> dict:
    engine._reset_execution_trace(request_id="r148g", goal=query, include_trace=True)
    obs = engine._build_query_frame_observation(query=query, selected_route=selected_route)
    engine._set_trace_value("query_frame", obs)
    engine._set_trace_value("route_decision", {"selected_route": selected_route})
    return obs


def test_english_ceo_output_unchanged_default_language() -> None:
    engine = OrchestrationEngine()
    engine._reset_execution_trace(request_id="r148g-en", goal="who is the CEO of Relyce Infotech", include_trace=True)
    out = engine._build_entity_lookup_response(
        goal="who is the CEO of Relyce Infotech",
        role_label="CEO",
        entity_label="Relyce Infotech",
        verification_state="candidate",
        policy_reason="entity_lookup_not_verified",
        evidence_rows=[
            {
                "title": "Relyce leadership",
                "link": "https://example.com/leadership",
                "snippet": "Relyce Infotech CEO is Ukenthiran A.",
                "company_match": True,
                "target_entity_match": True,
                "role_holder_detected": "Ukenthiran A",
                "role_applies_to_person": True,
                "supported_role": "ceo",
                "role_match": True,
                "rank_score": 0.7,
            }
        ],
        queries=["Relyce Infotech CEO"],
    )
    assert "Tamil/Tanglish summary:" not in out
    assert engine._trace_data.get("answer_language") == "en"
    assert engine._trace_data.get("language_preservation_status") == "english_default"


def test_tamil_founder_query_applies_tanglish_framing_with_names_unchanged(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    engine = OrchestrationEngine()
    _prepare(engine, "Microsoft யாரால் தொடங்கப்பட்டது?", selected_route="entity_lookup")
    handoff = engine._build_query_frame_entity_handoff(goal="Microsoft யாரால் தொடங்கப்பட்டது?")
    out = engine._build_entity_lookup_response(
        goal="Microsoft யாரால் தொடங்கப்பட்டது?",
        role_label="FOUNDER",
        entity_label="Microsoft",
        verification_state="candidate",
        policy_reason="entity_lookup_not_verified",
        evidence_rows=[
            {
                "title": "Microsoft founder history",
                "link": "https://example.com/history",
                "snippet": "Bill Gates founded Microsoft.",
                "company_match": True,
                "target_entity_match": True,
                "role_holder_detected": "Bill Gates",
                "role_applies_to_person": True,
                "supported_role": "founder",
                "role_match": True,
                "rank_score": 0.8,
            }
        ],
        queries=["who founded Microsoft"],
        lookup_type_hint=handoff["entity_handoff_lookup_type"],
    )
    assert "Tamil/Tanglish summary:" in out
    assert "Bill Gates" in out
    assert "who founded Microsoft" in out
    assert engine._trace_data.get("answer_language") == "ta"
    assert engine._trace_data.get("language_preservation_applied") is True


def test_tamil_linkedin_mode_preserved_with_tanglish_framing(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    engine = OrchestrationEngine()
    _prepare(engine, "Relyce Infotech LinkedIn கண்டுபிடி", selected_route="entity_lookup")
    handoff = engine._build_query_frame_entity_handoff(goal="Relyce Infotech LinkedIn கண்டுபிடி")
    out = engine._build_entity_lookup_response(
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
    assert "Tamil/Tanglish summary:" in out
    assert engine._trace_data.get("entity_answer_mode") == "profile_link_result"
    assert "ceo" not in out.lower()


def test_tamil_business_legitimacy_mode_preserved_with_tanglish_framing(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    monkeypatch.setenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "true")
    engine = OrchestrationEngine()
    _prepare(engine, "Relyce Infotech உண்மையான company ஆ?", selected_route="entity_lookup")
    handoff = engine._build_query_frame_entity_handoff(goal="Relyce Infotech உண்மையான company ஆ?")
    out = engine._build_entity_lookup_response(
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
    assert "Tamil/Tanglish summary:" in out
    assert "legal registration" in out.lower()
    assert engine._trace_data.get("entity_answer_mode") == "business_legitimacy_result"


def test_non_tamil_language_keeps_english_with_metadata_fallback() -> None:
    engine = OrchestrationEngine()
    engine._reset_execution_trace(request_id="r148g-ar", goal="من أسس Microsoft؟", include_trace=True)
    engine._set_trace_value(
        "query_frame",
        {
            "answer_language": "ar",
            "detected_language": "ar",
            "intent_family": "entity_lookup",
        },
    )
    out = engine._build_entity_lookup_response(
        goal="من أسس Microsoft؟",
        role_label="FOUNDER",
        entity_label="Microsoft",
        verification_state="candidate",
        policy_reason="entity_lookup_not_verified",
        evidence_rows=[
            {
                "title": "Microsoft founder history",
                "link": "https://example.com/history",
                "snippet": "Bill Gates founded Microsoft.",
                "company_match": True,
                "target_entity_match": True,
                "role_holder_detected": "Bill Gates",
                "role_applies_to_person": True,
                "supported_role": "founder",
                "role_match": True,
                "rank_score": 0.8,
            }
        ],
        queries=["who founded Microsoft"],
    )
    assert "Tamil/Tanglish summary:" not in out
    assert engine._trace_data.get("answer_language") == "ar"
    assert engine._trace_data.get("language_preservation_status") == "fallback_english"
    assert engine._trace_data.get("language_preservation_applied") is False


def test_doc_mode_protected_route_still_blocks_query_frame_assist(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "true")
    engine = OrchestrationEngine()
    obs = engine._build_query_frame_observation(query="who founded Microsoft", selected_route="doc_mode")
    decision = engine._decide_query_frame_route_assist(
        query_frame_observation=obs,
        selected_route="doc_mode",
        query_kind="entity_lookup",
        doc_context_active=True,
    )
    assert decision["route_assist_applied"] is False
    assert decision["route_assist_blocked_reason"] == "protected_route"
