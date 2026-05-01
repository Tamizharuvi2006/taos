from __future__ import annotations

from taos.core.understanding.search_intent_planner import SearchIntentPlanner

from .language_hint_detector import LanguageHintDetector
from .transliteration_normalizer import TransliterationNormalizer


class GlobalQueryNormalizer:
    def __init__(self) -> None:
        self._language = LanguageHintDetector()
        self._translit = TransliterationNormalizer()
        self._planner = SearchIntentPlanner()

    def normalize(self, query: str):
        language = self._language.detect(query)
        normalized = self._translit.normalize(query)
        frame = self._planner.plan(normalized)
        return {
            "language": language,
            "normalized_text": normalized,
            "intent_frame": frame,
            "search_queries": frame.search_plan.as_dict(),
        }
