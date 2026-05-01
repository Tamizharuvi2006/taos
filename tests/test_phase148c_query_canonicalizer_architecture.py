from __future__ import annotations

from taos.core.understanding.query_canonicalizer import CanonicalizationResult
from taos.core.understanding.query_frame import QueryFrameBuilder


def test_fast_path_keeps_existing_examples() -> None:
    frame = QueryFrameBuilder().build("who founded Microsoft")
    assert frame.intent == "entity_lookup"
    assert frame.lookup_type == "founder_lookup"
    assert frame.entity == "Microsoft"
    assert frame.source == "rule_based_fast_path"


def test_unsupported_non_english_safe_unknown_when_semantic_disabled(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_SEMANTIC_CANONICALIZER_ENABLED", "false")
    frame = QueryFrameBuilder().build("Microsoft സ്ഥാപിച്ചത് ആര്?")
    assert frame.intent == "unknown"
    assert frame.detected_language == "ml"
    assert "semantic_canonicalizer_disabled" in set(frame.warnings)


class _GlobalMockCanonicalizer:
    def __init__(self, payload: CanonicalizationResult) -> None:
        self._payload = payload

    def canonicalize(self, query: str) -> CanonicalizationResult:
        return self._payload


def test_mock_semantic_handles_arabic_founder(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_SEMANTIC_CANONICALIZER_ENABLED", "true")
    mock = _GlobalMockCanonicalizer(
        CanonicalizationResult(
            canonical_query="who founded Microsoft",
            intent="entity_lookup",
            lookup_type="founder_lookup",
            entity="Microsoft",
            role="founder",
            detected_language="ar",
            detected_script="Arabic",
            answer_language="ar",
            confidence=0.88,
            source="semantic_canonicalizer_mock",
        )
    )
    frame = QueryFrameBuilder(semantic_canonicalizer=mock).build("من أسس Microsoft؟")
    assert frame.canonical_query == "who founded Microsoft"
    assert frame.answer_language == "ar"
    assert frame.source == "semantic_canonicalizer_mock"


def test_mock_semantic_handles_german_ceo(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_SEMANTIC_CANONICALIZER_ENABLED", "true")
    mock = _GlobalMockCanonicalizer(
        CanonicalizationResult(
            canonical_query="who is the CEO of Microsoft",
            intent="entity_lookup",
            lookup_type="ceo_lookup",
            entity="Microsoft",
            role="ceo",
            detected_language="de",
            detected_script="Latin",
            answer_language="de",
            confidence=0.86,
            source="semantic_canonicalizer_mock",
        )
    )
    frame = QueryFrameBuilder(semantic_canonicalizer=mock).build("Wer ist der CEO von Microsoft?")
    assert frame.answer_language == "de"
    assert frame.lookup_type == "ceo_lookup"


def test_mock_semantic_handles_malayalam_founder(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_SEMANTIC_CANONICALIZER_ENABLED", "true")
    mock = _GlobalMockCanonicalizer(
        CanonicalizationResult(
            canonical_query="who founded Microsoft",
            intent="entity_lookup",
            lookup_type="founder_lookup",
            entity="Microsoft",
            role="founder",
            detected_language="ml",
            detected_script="Malayalam",
            answer_language="ml",
            confidence=0.86,
            source="semantic_canonicalizer_mock",
        )
    )
    frame = QueryFrameBuilder(semantic_canonicalizer=mock).build("Microsoft സ്ഥാപിച്ചത് ആര്?")
    assert frame.answer_language == "ml"


def test_mock_semantic_handles_japanese_ceo(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_SEMANTIC_CANONICALIZER_ENABLED", "true")
    mock = _GlobalMockCanonicalizer(
        CanonicalizationResult(
            canonical_query="who is the CEO of Microsoft",
            intent="entity_lookup",
            lookup_type="ceo_lookup",
            entity="Microsoft",
            role="ceo",
            detected_language="ja",
            detected_script="Japanese",
            answer_language="ja",
            confidence=0.86,
            source="semantic_canonicalizer_mock",
        )
    )
    frame = QueryFrameBuilder(semantic_canonicalizer=mock).build("MicrosoftのCEOは誰ですか？")
    assert frame.answer_language == "ja"


def test_builder_rejects_invalid_semantic_output(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_SEMANTIC_CANONICALIZER_ENABLED", "true")
    bad = _GlobalMockCanonicalizer(
        CanonicalizationResult(
            canonical_query="",
            intent="entity_lookup",
            lookup_type="founder_lookup",
            entity="",
            role="founder",
            detected_language="ar",
            detected_script="Arabic",
            answer_language="ar",
            confidence=0.95,
            source="semantic_canonicalizer_mock",
        )
    )
    frame = QueryFrameBuilder(semantic_canonicalizer=bad).build("من أسس Microsoft؟")
    assert frame.intent == "unknown"


def test_confidence_threshold_is_enforced(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_SEMANTIC_CANONICALIZER_ENABLED", "true")
    weak = _GlobalMockCanonicalizer(
        CanonicalizationResult(
            canonical_query="who founded Microsoft",
            intent="entity_lookup",
            lookup_type="founder_lookup",
            entity="Microsoft",
            role="founder",
            detected_language="ar",
            detected_script="Arabic",
            answer_language="ar",
            confidence=0.70,
            source="semantic_canonicalizer_mock",
        )
    )
    frame = QueryFrameBuilder(semantic_canonicalizer=weak).build("من أسس Microsoft؟")
    assert frame.intent == "unknown"


def test_original_query_preserved_and_search_queries_shape(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_FRAME_SEMANTIC_CANONICALIZER_ENABLED", "true")
    mock = _GlobalMockCanonicalizer(
        CanonicalizationResult(
            canonical_query="who founded Microsoft",
            intent="entity_lookup",
            lookup_type="founder_lookup",
            entity="Microsoft",
            role="founder",
            detected_language="ar",
            detected_script="Arabic",
            answer_language="ar",
            confidence=0.91,
            source="semantic_canonicalizer_mock",
        )
    )
    q = "من أسس Microsoft؟"
    frame = QueryFrameBuilder(semantic_canonicalizer=mock).build(q)
    assert frame.original_query == q
    assert frame.search_queries[0] == "who founded Microsoft"
    assert q in frame.search_queries
    assert frame.answer_language == "ar"
