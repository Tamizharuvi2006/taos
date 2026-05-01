from __future__ import annotations

from typing import Any, Dict, Optional

from .route_rules import (
    ADVICE_RE,
    COMPARISON_RE,
    DEFINITION_RE,
    DOC_RE,
    FRESHNESS_RE,
    GREETING_RE,
    HIGH_STAKES_RE,
    NEWS_RE,
    RESEARCH_RE,
    ROLE_RE,
    TASK_RE,
    TAMIL_SMALL_TALK_RE,
    RuleDecision,
)


def score_routes(normalized_query: str, context: Optional[Dict[str, Any]] = None) -> RuleDecision:
    text = str(normalized_query or "").strip()
    context = dict(context or {})
    scores = {
        "fast_message": 0.0,
        "no_search": 0.0,
        "fast_search": 0.0,
        "deep_search": 0.0,
        "news_search": 0.0,
        "official_search": 0.0,
        "comparison_search": 0.0,
        "doc_mode": 0.0,
        "task": 0.0,
        "clarification": 0.0,
    }
    matched = []

    if GREETING_RE.search(text) or TAMIL_SMALL_TALK_RE.search(text):
        scores["fast_message"] += 0.9
        matched.append("greeting")
    if DEFINITION_RE.search(text):
        scores["no_search"] += 0.75
        matched.append("definition")
    if FRESHNESS_RE.search(text):
        scores["fast_search"] += 0.65
        matched.append("freshness")
    if NEWS_RE.search(text):
        scores["news_search"] += 0.85
        matched.append("news")
    if RESEARCH_RE.search(text):
        scores["deep_search"] += 0.85
        matched.append("research")
    if COMPARISON_RE.search(text):
        scores["comparison_search"] += 0.8
        matched.append("comparison")
    if DOC_RE.search(text) or bool(context.get("has_active_doc")):
        scores["doc_mode"] += 0.9
        matched.append("doc")
    if TASK_RE.search(text):
        scores["task"] += 0.7
        matched.append("task")
    if HIGH_STAKES_RE.search(text):
        scores["official_search"] += 0.82
        matched.append("high_stakes")
        if ADVICE_RE.search(text):
            scores["clarification"] += 0.72
            matched.append("high_stakes_advice")
    if ROLE_RE.search(text):
        scores["official_search"] += 0.78
        matched.append("role_lookup")

    if scores["news_search"] and scores["fast_search"]:
        scores["news_search"] += 0.1
    if scores["no_search"] and scores["fast_search"]:
        scores["no_search"] -= 0.35
    if scores["comparison_search"] and scores["deep_search"]:
        scores["deep_search"] += 0.05

    route = max(scores, key=scores.get)
    confidence = max(0.0, min(0.99, scores[route]))
    return RuleDecision(
        route=route,
        confidence=confidence,
        reason="heuristic_route_score",
        matched_rules=matched,
        high_stakes=bool(HIGH_STAKES_RE.search(text)),
    )
