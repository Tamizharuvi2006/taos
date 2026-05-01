from __future__ import annotations

import pytest

from taos.core.documents.document_intent_router import document_intent_router


@pytest.mark.parametrize(
    ("prompt", "expected_mode"),
    [
        ("Tomorrow exam, give me very important 16 mark questions from these notes", "important_questions"),
        ("Give me quick last-minute revision for unit 3", "revision"),
        ("Compare supervised and unsupervised learning from this document deeply", "research_analysis"),
        ("Extract all formulas and definitions from this PDF", "extraction"),
        ("Turn this chapter into flashcards", "transformation"),
        ("How should I study this in 2 days?", "general_doc_assist"),
    ],
)
def test_eval_prompts_route_to_expected_modes(prompt: str, expected_mode: str):
    routed = document_intent_router(prompt)
    assert routed["mode_selected"] == expected_mode
    assert routed["mode_source"] in {"auto", "default", "override"}

