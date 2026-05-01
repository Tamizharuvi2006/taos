from __future__ import annotations

import pytest

from taos.core.feedback.feedback_memory import FeedbackMemoryEngine
from taos.infra.persistence.store import InMemoryStore


@pytest.mark.asyncio
async def test_feedback_memory_store_and_retrieve():
    engine = FeedbackMemoryEngine(store=InMemoryStore())

    await engine.add_feedback(
        user_id="alice",
        query="React vs Vue performance",
        bad_answer="Vue is always faster in every case.",
        corrected_answer="Performance depends on app architecture and rendering patterns.",
        tags=["comparison", "frontend"],
        rating=-1,
    )

    matches = await engine.retrieve_relevant_feedback(
        user_id="alice",
        query="Compare React and Vue performance",
        top_k=3,
    )

    assert len(matches) >= 1
    hints = engine.build_context_hints(matches)
    assert len(hints) >= 1
    assert "Corrective guidance" in hints[0]


@pytest.mark.asyncio
async def test_feedback_memory_deduplicates_same_correction():
    engine = FeedbackMemoryEngine(store=InMemoryStore())

    first_id = await engine.add_feedback(
        user_id="alice",
        query="React vs Vue performance",
        bad_answer="Bad 1",
        corrected_answer="Depends on workload.",
    )
    second_id = await engine.add_feedback(
        user_id="alice",
        query="React vs Vue performance",
        bad_answer="Bad 2",
        corrected_answer="Depends on workload.",
    )

    assert first_id == second_id
