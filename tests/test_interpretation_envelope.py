from __future__ import annotations

from taos.core.semantic.intent_classifier import ClassificationResult, DomainType, IntentType
from taos.core.semantic.interpretation import RequestInterpreter
from taos.core.semantic.query_rewriter import RewriteResult


def test_normalize_preserves_urls_code_and_quotes():
    interpreter = RequestInterpreter()
    raw = 'hey   check   "same text"  https://example.com/a?x=1  and  `const x = 1;`'
    normalized = interpreter.normalize_query(raw)
    assert '"same text"' in normalized
    assert "https://example.com/a?x=1" in normalized
    assert "`const x = 1;`" in normalized
    assert "  " not in normalized


def test_detect_language_profile_tamil_translit_mixed():
    interpreter = RequestInterpreter()
    raw = "macha intha latest update enna bro"
    normalized = interpreter.normalize_query(raw)
    profile = interpreter.detect_language_profile(raw, normalized)
    assert profile["transliteration_detected"] is True
    assert profile["mixed_language_flag"] is True
    assert 0.0 <= float(profile["language_confidence"]) <= 1.0


def test_build_envelope_contains_required_sections():
    interpreter = RequestInterpreter()
    classification = ClassificationResult(
        intent=IntentType.TASK,
        domain=DomainType.GENERAL,
        confidence=0.74,
        metadata={"route_label": "standard_task", "route_source": "heuristic", "route_confidence": 0.74},
    )
    rewrite = RewriteResult(
        original="older one what done",
        rewritten="Summarize the previously discussed item in a concise way.",
        was_modified=True,
        context_injected=False,
        reason="canonicalized fragment",
    )
    envelope = interpreter.build_envelope(
        raw_query="older one what done",
        classification=classification,
        rewrite=rewrite,
        has_context=False,
        has_active_doc=False,
    )
    assert envelope["raw_query"] == "older one what done"
    assert "normalized_query" in envelope
    assert "rewritten_query" in envelope
    assert "language_profile" in envelope
    assert "routing_profile" in envelope
    assert "route_decision" in envelope
    decision = envelope["route_decision"]
    assert decision["selected_route"] in {
        "micro_fast",
        "standard_answer",
        "standard_fsm_task",
        "deep_research",
        "entity_lookup",
        "document_pipeline",
        "transform_pipeline",
        "clarification",
    }


def test_entity_lookup_query_kind_and_route_selected():
    interpreter = RequestInterpreter()
    classification = ClassificationResult(
        intent=IntentType.RESEARCH,
        domain=DomainType.GENERAL,
        confidence=0.88,
        metadata={"route_label": "deep_research", "route_source": "heuristic", "route_confidence": 0.88},
    )
    rewrite = RewriteResult(
        original="who is the ceo of relyce infotech",
        rewritten="who is the ceo of relyce infotech",
        was_modified=False,
        context_injected=False,
        reason="no changes needed",
    )
    envelope = interpreter.build_envelope(
        raw_query="who is the ceo of relyce infotech",
        classification=classification,
        rewrite=rewrite,
        has_context=False,
        has_active_doc=False,
    )
    routing = envelope["routing_profile"]
    decision = envelope["route_decision"]
    assert routing.get("query_kind") == "entity_lookup"
    assert decision.get("selected_route") == "entity_lookup"
