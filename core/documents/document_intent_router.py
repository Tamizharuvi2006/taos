"""Document intent routing helpers for adaptive grounded document workflows."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

CANONICAL_DOCUMENT_MODES = {
    "qa",
    "important_questions",
    "mark_questions",
    "mark_answers",
    "revision",
    "research_analysis",
    "extraction",
    "transformation",
    "test_generation",
    "general_doc_assist",
}

ALLOWED_MARK_FORMATS = {"1", "2", "5", "10", "16"}
ALLOWED_OUTPUT_FORMATS = {"paragraph", "bullet", "table", "flashcards", "outline"}


def normalize_mode(value: Optional[str]) -> Optional[str]:
    clean = str(value or "").strip().lower()
    if not clean:
        return None
    return clean if clean in CANONICAL_DOCUMENT_MODES else None


def normalize_mark_format(value: Optional[str]) -> Optional[str]:
    clean = str(value or "").strip()
    if not clean:
        return None
    return clean if clean in ALLOWED_MARK_FORMATS else None


def normalize_output_format(value: Optional[str]) -> Optional[str]:
    clean = str(value or "").strip().lower()
    if not clean:
        return None
    return clean if clean in ALLOWED_OUTPUT_FORMATS else None


def _has_any(text: str, phrases: list[str]) -> bool:
    return any(p in text for p in phrases)


def document_intent_router(
    question: str,
    mode_override: Optional[str] = None,
    hints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve adaptive document mode using:
    1) explicit valid override
    2) heuristic auto detection
    3) fallback qa
    """
    hints = dict(hints or {})
    override = normalize_mode(mode_override)
    if override:
        return {
            "mode_selected": override,
            "mode_source": "override",
            "intent_detected": override,
            "signals": ["explicit_override"],
        }

    q = str(question or "").strip().lower()
    signals: list[str] = []

    mark_hint = normalize_mark_format(str(hints.get("mark_format") or ""))
    if mark_hint:
        signals.append(f"mark_format:{mark_hint}")

    important_phrase = _has_any(q, ["important question", "very important question", "expected question", "likely exam question"])
    important_pattern = bool(re.search(r"\b(very\s+)?important\b.*\bquestions?\b", q))
    if important_phrase or important_pattern:
        return {
            "mode_selected": "important_questions",
            "mode_source": "auto",
            "intent_detected": "important_questions",
            "signals": signals + ["important_questions_phrase"],
        }

    if re.search(r"\b(1|2|5|10|16)\s*[- ]?mark\b", q) and _has_any(
        q,
        ["answer", "write", "explain", "for ", "in ", "give answer", "short answer", "long answer"],
    ):
        return {
            "mode_selected": "mark_answers",
            "mode_source": "auto",
            "intent_detected": "mark_answers",
            "signals": signals + ["mark_answer_phrase"],
        }

    if re.search(r"\b(1|2|5|10|16)\s*[- ]?mark\b", q):
        return {
            "mode_selected": "mark_questions",
            "mode_source": "auto",
            "intent_detected": "mark_questions",
            "signals": signals + ["mark_question_phrase"],
        }

    if _has_any(q, ["mock test", "model test", "practice test", "question paper", "viva question", "quiz from this"]):
        return {
            "mode_selected": "test_generation",
            "mode_source": "auto",
            "intent_detected": "test_generation",
            "signals": signals + ["test_generation_phrase"],
        }

    if _has_any(q, ["revision", "last-minute", "last minute", "quick summary", "quick revision", "high-yield"]):
        return {
            "mode_selected": "revision",
            "mode_source": "auto",
            "intent_detected": "revision",
            "signals": signals + ["revision_phrase"],
        }

    if _has_any(q, ["extract", "list all", "definitions", "formulas", "keywords", "dates", "theorems"]):
        return {
            "mode_selected": "extraction",
            "mode_source": "auto",
            "intent_detected": "extraction",
            "signals": signals + ["extraction_phrase"],
        }

    if _has_any(q, ["convert", "turn this into", "flashcards", "cheat sheet", "outline", "table format"]):
        return {
            "mode_selected": "transformation",
            "mode_source": "auto",
            "intent_detected": "transformation",
            "signals": signals + ["transformation_phrase"],
        }

    if _has_any(q, ["compare", "analyze", "analysis", "evaluate", "strength", "weakness", "research", "critique"]):
        return {
            "mode_selected": "research_analysis",
            "mode_source": "auto",
            "intent_detected": "research_analysis",
            "signals": signals + ["analysis_phrase"],
        }

    if _has_any(
        q,
        [
            "what should i focus",
            "how should i study",
            "study plan",
            "is this easy or difficult",
            "where to start",
            "simplify this for me",
            "how to prepare",
        ],
    ):
        return {
            "mode_selected": "general_doc_assist",
            "mode_source": "auto",
            "intent_detected": "general_doc_assist",
            "signals": signals + ["guidance_phrase"],
        }

    return {
        "mode_selected": "qa",
        "mode_source": "default",
        "intent_detected": "qa",
        "signals": signals + ["fallback_qa"],
    }
