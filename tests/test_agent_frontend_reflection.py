from __future__ import annotations

from taos.apps.api.routes.agent import (
    _build_frontend_hints,
    _build_source_cards,
    _build_trust_badges,
    _build_uncertainty_box,
    _parse_answer_sections,
)
from taos.apps.api.schemas.agent import AgentResponse


def test_parse_answer_sections_extracts_structured_blocks():
    answer = (
        "Answer\n"
        "Update is partially confirmed.\n\n"
        "Evidence\n"
        "- Source A confirms event.\n"
        "- Source B disputes attribution.\n\n"
        "What's still unclear\n"
        "- Attribution remains unverified.\n\n"
        "Bottom line\n"
        "Treat as provisional."
    )
    sections = _parse_answer_sections(answer)
    keys = [s["key"] for s in sections]
    assert "what_happened" in keys
    assert "key_evidence" in keys
    assert "what_is_unclear" in keys
    assert "bottom_line" in keys


def test_build_uncertainty_box_for_conflict_and_high_stakes():
    box = _build_uncertainty_box(
        "Answer text",
        {
            "conflict_detected": True,
            "high_stakes_mode": True,
            "official_source_required": True,
            "official_source_found": False,
            "signal": "conflicting",
        },
    )
    assert box is not None
    joined = " ".join(box.get("messages") or []).lower()
    assert "not fully confirmed" in joined
    assert "sources disagree" in joined
    assert "official confirmation" in joined


def test_build_source_cards_infers_domain_and_type():
    cards = _build_source_cards(
        [
            {"title": "RBI statement", "url": "https://www.rbi.org.in/x", "tier": "official", "published_at": "2026-04-11"},
            "https://example.com/report",
        ],
        {"evidence": "Strong"},
    )
    assert len(cards) == 2
    assert cards[0]["source_type"] == "official"
    assert cards[0]["domain"] == "rbi.org.in"
    assert cards[1]["source_type"] == "reporting"


def test_build_trust_badges_contains_required_labels():
    badges = _build_trust_badges(
        {
            "confidence": "Medium",
            "freshness": "High",
            "agreement": "medium",
            "conflict_detected": True,
            "high_stakes_mode": True,
        }
    )
    labels = {b["label"] for b in badges}
    assert {"Trust", "Freshness", "Agreement", "Conflict", "High-stakes"} <= labels


def test_agent_response_accepts_frontend_reflection_fields():
    response = AgentResponse.model_validate(
        {
            "answer": "Answer",
            "intent": "research",
            "domain": "general",
            "mode": "deep",
            "confidence": 0.66,
            "trust_badges": [{"key": "trust", "label": "Trust", "value": "Medium", "tone": "neutral"}],
            "uncertainty_box": {
                "level": "caution",
                "title": "What to treat carefully",
                "messages": ["This is not fully confirmed yet."],
                "high_stakes": True,
            },
            "source_cards": [
                {
                    "title": "RBI statement",
                    "url": "https://www.rbi.org.in/x",
                    "domain": "rbi.org.in",
                    "source_type": "official",
                }
            ],
            "answer_sections": [
                {
                    "key": "what_happened",
                    "title": "What happened",
                    "content": "Update is partially confirmed.",
                    "bullets": [],
                }
            ],
            "frontend_hints": {
                "route_label": "deep_research",
                "progress_stages": ["Searching sources", "Ranking evidence", "Preparing answer"],
                "high_stakes": True,
                "official_source_required": True,
                "official_source_found": False,
            },
        }
    )
    assert response.frontend_hints is not None
    assert response.frontend_hints.route_label == "deep_research"
    assert response.source_cards[0].source_type == "official"


def test_build_frontend_hints_handles_new_route_specific_progress_stages():
    fast = _build_frontend_hints("fast_search", {"high_stakes_mode": False})
    official = _build_frontend_hints(
        "official_search",
        {"high_stakes_mode": True, "official_source_required": True, "official_source_found": True},
    )
    assert fast["progress_stages"] == ["Checking live sources", "Preparing answer"]
    assert official["progress_stages"][0] == "Checking official sources"
    assert official["high_stakes"] is True
