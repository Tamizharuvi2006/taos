from __future__ import annotations

import pytest

from taos.core.understanding.query_frame import QueryFrameBuilder


@pytest.mark.parametrize(
    ("query", "canonical_query", "lookup_type", "entity", "role", "answer_language"),
    [
        (
            "who founded Microsoft",
            "who founded Microsoft",
            "founder_lookup",
            "Microsoft",
            "founder",
            "en",
        ),
        (
            "who is the CEO of Microsoft",
            "who is the CEO of Microsoft",
            "ceo_lookup",
            "Microsoft",
            "ceo",
            "en",
        ),
        (
            "find LinkedIn of Relyce Infotech",
            "find LinkedIn of Relyce Infotech",
            "linkedin_profile",
            "Relyce Infotech",
            "",
            "en",
        ),
        (
            "is Relyce Infotech a real company",
            "is Relyce Infotech a real company",
            "business_legitimacy",
            "Relyce Infotech",
            "",
            "en",
        ),
        (
            "Microsoft யாரால் தொடங்கப்பட்டது?",
            "who founded Microsoft",
            "founder_lookup",
            "Microsoft",
            "founder",
            "ta",
        ),
        (
            "Microsoft CEO யார்?",
            "who is the CEO of Microsoft",
            "ceo_lookup",
            "Microsoft",
            "ceo",
            "ta",
        ),
        (
            "Relyce Infotech LinkedIn கண்டுபிடி",
            "find LinkedIn of Relyce Infotech",
            "linkedin_profile",
            "Relyce Infotech",
            "",
            "ta",
        ),
        (
            "Relyce Infotech உண்மையான company ஆ?",
            "is Relyce Infotech a real company",
            "business_legitimacy",
            "Relyce Infotech",
            "",
            "ta",
        ),
        (
            "Microsoft की स्थापना किसने की?",
            "who founded Microsoft",
            "founder_lookup",
            "Microsoft",
            "founder",
            "hi",
        ),
        (
            "Microsoft का CEO कौन है?",
            "who is the CEO of Microsoft",
            "ceo_lookup",
            "Microsoft",
            "ceo",
            "hi",
        ),
        (
            "¿Quién fundó Microsoft?",
            "who founded Microsoft",
            "founder_lookup",
            "Microsoft",
            "founder",
            "es",
        ),
        (
            "¿Quién es el CEO de Microsoft?",
            "who is the CEO of Microsoft",
            "ceo_lookup",
            "Microsoft",
            "ceo",
            "es",
        ),
    ],
)
def test_query_frame_builder_supported_queries(
    query: str,
    canonical_query: str,
    lookup_type: str,
    entity: str,
    role: str,
    answer_language: str,
) -> None:
    frame = QueryFrameBuilder().build(query)

    assert frame.original_query == query
    assert frame.canonical_query == canonical_query
    assert frame.intent == "entity_lookup"
    assert frame.lookup_type == lookup_type
    assert frame.entity == entity
    assert frame.role == role
    assert frame.answer_language == answer_language
    assert frame.detected_language == answer_language
    assert frame.search_queries
    assert frame.search_queries[0] == canonical_query
    if query != canonical_query:
        assert query in frame.search_queries


def test_query_frame_detects_scripts_for_supported_queries() -> None:
    builder = QueryFrameBuilder()

    assert builder.build("Microsoft யாரால் தொடங்கப்பட்டது?").detected_script == "Tamil"
    assert builder.build("Microsoft की स्थापना किसने की?").detected_script == "Devanagari"
    assert builder.build("¿Quién fundó Microsoft?").detected_script == "Latin"


def test_query_frame_unknown_query_stays_unknown() -> None:
    frame = QueryFrameBuilder().build("tell me something nice")

    assert frame.intent == "unknown"
    assert frame.lookup_type == "unknown"
    assert frame.entity == ""
    assert frame.search_queries == ["tell me something nice"]
