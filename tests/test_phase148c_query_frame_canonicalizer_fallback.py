from __future__ import annotations

from taos.core.understanding.query_frame import (
    CanonicalizationResult,
    QueryFrameBuilder,
)


def test_unsupported_non_english_query_returns_unknown_without_crash() -> None:
    frame = QueryFrameBuilder().build("தமிழில் சில வித்தியாசமான வாக்கியம்")
    assert frame.intent in {"unknown", "entity_lookup"}
    # Contract: no crash + always a valid frame object with canonical query text.
    assert isinstance(frame.canonical_query, str)
    assert frame.detected_language == "ta"


class _MockCanonicalizer:
    def canonicalize(
        self,
        *,
        original_query: str,
        normalized_query: str,
        detected_language: str,
        detected_script: str,
    ) -> CanonicalizationResult:
        return CanonicalizationResult(
            canonical_query="who founded Microsoft",
            intent="entity_lookup",
            lookup_type="founder_lookup",
            entity="Microsoft",
            role="founder",
            detected_language=detected_language,
            detected_script=detected_script,
            answer_language=detected_language,
            confidence=0.93,
            source="mock_semantic_canonicalizer",
        )


def test_mock_canonicalizer_can_produce_valid_query_frame() -> None:
    builder = QueryFrameBuilder(canonicalizer=_MockCanonicalizer())
    frame = builder.build("Microsoft நிறுவனர் யார்?")
    assert frame.intent == "entity_lookup"
    assert frame.lookup_type == "founder_lookup"
    assert frame.entity == "Microsoft"
    assert frame.role == "founder"
    assert frame.canonical_query == "who founded Microsoft"
    assert frame.source == "mock_semantic_canonicalizer"
