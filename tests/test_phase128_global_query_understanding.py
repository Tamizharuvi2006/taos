from __future__ import annotations

from taos.core.understanding.global_query_normalizer import GlobalQueryNormalizer
from taos.core.understanding.language_hint_detector import LanguageHintDetector
from taos.core.understanding.transliteration_normalizer import TransliterationNormalizer


def test_mixed_english_tamil_hint_is_detected() -> None:
    detected = LanguageHintDetector().detect("claude india work agala?")
    assert detected["mixed"] is True
    assert detected["primary"] == "mixed_english"


def test_transliteration_normalizer_preserves_product_entity() -> None:
    normalized = TransliterationNormalizer().normalize("claude india work agala?")
    assert "claude" in normalized.lower()
    assert "not working" in normalized.lower()


def test_global_query_normalizer_understands_broken_access_question() -> None:
    result = GlobalQueryNormalizer().normalize("claude india work agala?")
    frame = result["intent_frame"]
    assert frame.entities["country"] == "India"
    assert frame.entities["product"] == "Claude"
    assert frame.intent in {"rumour_verification", "troubleshooting"}


def test_no_overcorrection_of_entity_names() -> None:
    result = GlobalQueryNormalizer().normalize("gemini not working in europe true?")
    frame = result["intent_frame"]
    assert frame.entities["product"] == "Gemini"
    assert "Gemini" in frame.normalized_question
