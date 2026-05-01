from __future__ import annotations

import pytest

from taos.orchestration.engine import OrchestrationEngine


REQUIRED_KEYS = {
    "original_query",
    "canonical_query",
    "detected_language",
    "detected_script",
    "intent_family",
    "entity_name",
    "requested_role",
    "profile_target",
    "evidence_need",
    "answer_language",
    "current_selected_route",
    "query_frame_suggested_family",
    "route_alignment",
    "mismatch_reason",
}


@pytest.mark.parametrize(
    ("query", "selected_route", "expect_lang"),
    [
        ("Microsoft யாரால் தொடங்கப்பட்டது?", "entity_lookup", "ta"),
        ("Microsoft CEO யார்?", "entity_lookup", "ta"),
        ("Relyce Infotech LinkedIn கண்டுபிடி", "entity_lookup", "ta"),
        ("Relyce Infotech உண்மையான company ஆ?", "entity_lookup", "ta"),
        ("Microsoft की स्थापना किसने की?", "entity_lookup", "hi"),
        ("Microsoft का CEO कौन है?", "entity_lookup", "hi"),
        ("Relyce Infotech का LinkedIn ढूंढो", "entity_lookup", "hi"),
        ("क्या Relyce Infotech असली कंपनी है?", "entity_lookup", "hi"),
        ("¿Quién fundó Microsoft?", "entity_lookup", "es"),
        ("¿Quién es el CEO de Microsoft?", "entity_lookup", "es"),
        ("Encuentra el LinkedIn de Relyce Infotech", "entity_lookup", "es"),
        ("¿Relyce Infotech es una empresa real?", "entity_lookup", "es"),
        ("Qui a fondé Microsoft ?", "entity_lookup", "fr"),
        ("Qui est le PDG de Microsoft ?", "entity_lookup", "fr"),
        ("Trouve le LinkedIn de Relyce Infotech", "entity_lookup", "fr"),
        ("Relyce Infotech est-elle une vraie entreprise ?", "entity_lookup", "fr"),
    ],
)
def test_multilingual_query_frame_observe_fields_present(query: str, selected_route: str, expect_lang: str) -> None:
    observed = OrchestrationEngine()._build_query_frame_observation(query=query, selected_route=selected_route)
    assert REQUIRED_KEYS.issubset(set(observed.keys()))
    assert observed["original_query"] == query
    assert observed["detected_language"] == expect_lang
    assert observed["answer_language"] == expect_lang
    assert observed["current_selected_route"] == selected_route
    assert observed["query_frame_suggested_family"] in {"entity_lookup", "unknown"}
    assert observed["route_alignment"] in {"aligned", "mismatch", "unknown"}
    if observed["route_alignment"] == "mismatch":
        assert observed["mismatch_reason"] in {
            "current_route_standard_but_query_frame_entity_lookup",
            "current_route_research_but_query_frame_entity_lookup",
            "query_frame_low_confidence",
            "query_frame_unsupported",
            "route_unknown",
        }


def test_query_frame_mismatch_reason_is_clear_for_standard_route() -> None:
    observed = OrchestrationEngine()._build_query_frame_observation(
        query="Microsoft யாரால் தொடங்கப்பட்டது?",
        selected_route="standard_task",
    )
    assert observed["route_alignment"] == "mismatch"
    assert observed["mismatch_reason"] == "current_route_standard_but_query_frame_entity_lookup"


def test_query_frame_mismatch_reason_is_clear_for_research_route() -> None:
    observed = OrchestrationEngine()._build_query_frame_observation(
        query="¿Quién fundó Microsoft?",
        selected_route="deep_research",
    )
    assert observed["route_alignment"] == "mismatch"
    assert observed["mismatch_reason"] == "current_route_research_but_query_frame_entity_lookup"


def test_query_frame_telemetry_counters_observe_only() -> None:
    engine = OrchestrationEngine()
    engine._reset_execution_trace(request_id="r1", goal="g", include_trace=True)
    observed = engine._build_query_frame_observation(
        query="Qui est le PDG de Microsoft ?",
        selected_route="entity_lookup",
    )
    engine._set_query_frame_telemetry(observed)
    assert engine._trace_data["query_frame_aligned_count"] == 1
    assert engine._trace_data["query_frame_mismatch_count"] == 0
    assert engine._trace_data["query_frame_unknown_count"] == 0
    assert engine._trace_data["query_frame_supported_multilingual_count"] == 1

