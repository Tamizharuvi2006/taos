from __future__ import annotations

import pytest

from taos.core.persistence.firestore_memory import FirestoreMemorySchema
from taos.infra.persistence.store import InMemoryStore


@pytest.mark.asyncio
async def test_firestore_schema_feedback_and_plan_storage():
    svc = FirestoreMemorySchema(store=InMemoryStore())
    user_id = "alice"

    fb_id = await svc.upsert_feedback(
        user_id=user_id,
        query="Compare React and Vue",
        bad_answer="React always faster",
        corrected_answer="Depends on workload",
        confidence=0.85,
        tags=["comparison"],
    )
    assert fb_id.startswith("fb_")

    plan_id = await svc.store_plan(
        user_id=user_id,
        goal="Build API and deploy",
        plan_steps=[{"id": "s1", "action": "build"}],
        outcome="success",
        confidence=0.9,
        cost=0.1,
        latency=1200.0,
        tags=["task"],
    )
    assert plan_id.startswith("plan_")

    docs = await svc.store.list("plans", user_id=user_id)
    assert len(docs) >= 1


@pytest.mark.asyncio
async def test_firestore_schema_tool_stats_and_execution_log():
    svc = FirestoreMemorySchema(store=InMemoryStore())
    svc._settings.execution_sampling_rate = 1.0
    user_id = "bob"

    await svc.update_tool_stats(
        user_id=user_id,
        tool_name="web_search",
        success=True,
        latency=300.0,
        cost=0.01,
    )
    await svc.log_execution(
        user_id=user_id,
        execution_id="exec_1",
        goal="test goal",
        steps=[{"step_id": "s1", "success": True}],
        agents_used=["research_agent"],
        latency=400.0,
        cost=0.02,
        success=True,
    )

    stats_doc = await svc.store.get("tool_stats", "tool_web_search", user_id=user_id)
    assert stats_doc is not None
    assert float(stats_doc["success_rate"]) >= 0.0
    exec_doc = await svc.store.get("executions", "exec_1", user_id=user_id)
    assert exec_doc is not None


@pytest.mark.asyncio
async def test_firestore_schema_research_profile_semantic_retrieval():
    svc = FirestoreMemorySchema(store=InMemoryStore())
    user_id = "research_user"

    profile_id = await svc.store_research_profile(
        user_id=user_id,
        query="Who is the CEO of OpenAI and what is their background?",
        answer="Sam Altman is the CEO of OpenAI. The profile includes leadership background and recent role context.",
        source_rows=[{"title": "OpenAI leadership", "link": "https://openai.com", "tier": "official"}],
        agreement={"official_source_found": True, "agreement_level": "medium"},
        related_questions=["What is the latest official leadership update?"],
        entity_hints=["openai ceo profile"],
    )
    assert profile_id.startswith("rp_")

    rows = await svc.retrieve_research_profiles(
        user_id=user_id,
        query="openai ceo profile and background",
        limit=2,
        min_similarity=0.1,
    )
    assert rows
    assert str(rows[0].get("answer") or "").lower().find("ceo") >= 0
    assert float(rows[0].get("retrieval_score") or 0.0) > 0.0
