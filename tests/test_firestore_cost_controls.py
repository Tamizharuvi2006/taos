from __future__ import annotations

import pytest

from taos.core.persistence.firestore_memory import FirestoreMemorySchema
from taos.infra.persistence.store import InMemoryStore


@pytest.mark.asyncio
async def test_execution_sampling_and_truncation(monkeypatch):
    svc = FirestoreMemorySchema(store=InMemoryStore())
    monkeypatch.setattr(svc._settings, "execution_sampling_rate", 1.0)
    monkeypatch.setattr(svc._settings, "max_stored_field_bytes", 64)
    user_id = "sampler"
    big_steps = [{"step_id": "s1", "payload": "x" * 1000}]

    await svc.log_execution(
        user_id=user_id,
        execution_id="exec_1",
        goal="g",
        steps=big_steps,
        agents_used=["execution_agent"],
        latency=1200.0,
        cost=0.01,
        success=True,
    )
    doc = await svc.store.get("executions", "exec_1", user_id=user_id)
    assert doc is not None
    assert isinstance(doc.get("steps"), dict)
    assert doc["steps"].get("truncated") is True


@pytest.mark.asyncio
async def test_execution_retention_prunes_old_records(monkeypatch):
    svc = FirestoreMemorySchema(store=InMemoryStore())
    monkeypatch.setattr(svc._settings, "execution_sampling_rate", 1.0)
    monkeypatch.setattr(svc._settings, "max_execution_history_records", 3)
    user_id = "retention"

    for i in range(5):
        await svc.log_execution(
            user_id=user_id,
            execution_id=f"exec_{i}",
            goal="g",
            steps=[{"step_id": f"s{i}"}],
            agents_used=["execution_agent"],
            latency=1000.0 + i,
            cost=0.01,
            success=True,
        )
    docs = await svc.store.list("executions", user_id=user_id, limit=20)
    assert len(docs) == 3
