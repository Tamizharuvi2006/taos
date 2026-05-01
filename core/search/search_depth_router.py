from __future__ import annotations

from dataclasses import dataclass
import re


_FRESHNESS_HINTS = re.compile(r"\b(latest|current|today|now|recent|newest|live|as of|202[0-9]|this week|this month)\b", re.I)
_DEFINITION_HINTS = re.compile(r"\b(what is|define|meaning of|explain)\b", re.I)
_RESEARCH_HINTS = re.compile(r"\b(research|deeply|analyze|analysis|trend|market|forecast|outlook)\b", re.I)
_COMPARISON_HINTS = re.compile(r"\b(compare|comparison|vs|versus|best|pros and cons|better)\b", re.I)
_OFFICIAL_HINTS = re.compile(r"\b(official|source[- ]of[- ]record|legal|medical|financial|tax|immigration|regulatory|policy|regulation|compliance|safety-critical|api|docs|documentation|sdk|pricing|release notes|changelog)\b", re.I)
_NEWS_HINTS = re.compile(r"\b(news|headline|breaking|update|updates|today|current status|latest news)\b", re.I)
_PRICE_HINTS = re.compile(r"\b(price|stock|quote|ceo|release date|version)\b", re.I)
_ROLE_HINTS = re.compile(r"\b(ceo|founder|co-?founder|chairman|president|governor)\b", re.I)
_CHANGE_HINTS = re.compile(r"\b(changed|changes|release|released|models?|api)\b", re.I)
_RUMOUR_ACCESS_HINTS = re.compile(
    r"\b(rumou?r|heard|news|claim|blocked|blocking|block|banned|banning|ban|restricted|restricting|"
    r"unavailable|access|lovking|bloacking|bloking|baning|restrictng)\b",
    re.I,
)


@dataclass
class SearchDepthDecision:
    mode: str
    confidence: float
    reason: str


class SearchDepthRouter:
    VALID_MODES = {
        "no_search",
        "fast_search",
        "deep_search",
        "news_search",
        "official_search",
        "comparison_search",
    }

    def route(self, query: str) -> SearchDepthDecision:
        text = str(query or "").strip()
        normalized = text.lower()
        if not normalized:
            return SearchDepthDecision(mode="no_search", confidence=0.5, reason="empty_query")

        freshness = bool(_FRESHNESS_HINTS.search(normalized))
        if _RUMOUR_ACCESS_HINTS.search(normalized) and re.search(r"\b(india|country|region|available|supported)\b", normalized):
            return SearchDepthDecision(mode="news_search", confidence=0.9, reason="rumour_access_claim_query")
        if _NEWS_HINTS.search(normalized) and freshness:
            return SearchDepthDecision(mode="news_search", confidence=0.93, reason="fresh_news_query")
        if freshness and re.search(r"\bwhat changed\b", normalized):
            return SearchDepthDecision(mode="deep_search", confidence=0.88, reason="recent_change_research_query")
        if freshness and _CHANGE_HINTS.search(normalized) and re.search(r"\b(openai|model|models|api)\b", normalized):
            return SearchDepthDecision(mode="news_search", confidence=0.89, reason="fresh_model_change_query")
        if _OFFICIAL_HINTS.search(normalized):
            return SearchDepthDecision(mode="official_search", confidence=0.88, reason="official_or_high_stakes_query")
        if _ROLE_HINTS.search(normalized):
            return SearchDepthDecision(mode="official_search", confidence=0.87, reason="current_role_official_source_query")
        if _COMPARISON_HINTS.search(normalized):
            return SearchDepthDecision(mode="comparison_search", confidence=0.86, reason="comparison_query")
        if _RESEARCH_HINTS.search(normalized):
            return SearchDepthDecision(mode="deep_search", confidence=0.9, reason="explicit_research_query")
        if _DEFINITION_HINTS.search(normalized) and not freshness:
            return SearchDepthDecision(mode="no_search", confidence=0.9, reason="definition_query")
        if freshness or _PRICE_HINTS.search(normalized):
            return SearchDepthDecision(mode="fast_search", confidence=0.84, reason="current_lookup_query")
        return SearchDepthDecision(mode="no_search", confidence=0.55, reason="no_search_signal")
