from __future__ import annotations

import pytest

from taos.core.tasks.persistent_manager import PersistentTaskManager
from taos.core.tasks.task_model import TriggerType
from taos.infra.persistence.store import InMemoryStore


@pytest.mark.asyncio
async def test_low_confidence_retry_then_success(monkeypatch):
    manager = PersistentTaskManager(store=InMemoryStore(), user_id="u_conf")
    task = await manager.create_task(
        goal="Summarize research",
        trigger_type=TriggerType.MANUAL,
        max_retries=2,
        min_confidence=0.7,
        retry_policy={"low_confidence": 1},
    )

    calls = {"n": 0}

    async def fake_run_once(*, task, request_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "success": True,
                "formatted_response": "draft",
                "confidence": 0.42,
                "total_cost": 0.0,
            }
        return {
            "success": True,
            "formatted_response": "final",
            "confidence": 0.91,
            "total_cost": 0.0,
        }

    monkeypatch.setattr(manager, "_run_task_once", fake_run_once)
    monkeypatch.setattr(manager, "_retry_backoff_seconds", lambda *args, **kwargs: 0.0)

    execution = await manager.execute_task(task.task_id)

    assert execution.success is True
    assert execution.attempts == 2
    assert execution.retry_count == 1
    assert execution.failure_type == ""
    assert execution.confidence >= 0.9


@pytest.mark.asyncio
async def test_auth_failure_does_not_retry(monkeypatch):
    manager = PersistentTaskManager(store=InMemoryStore(), user_id="u_auth")
    task = await manager.create_task(
        goal="Fetch protected resource",
        trigger_type=TriggerType.MANUAL,
        max_retries=3,
    )

    async def fake_run_once(*, task, request_id):
        return {"success": False, "error": "403 forbidden"}

    monkeypatch.setattr(manager, "_run_task_once", fake_run_once)
    monkeypatch.setattr(manager, "_retry_backoff_seconds", lambda *args, **kwargs: 0.0)

    execution = await manager.execute_task(task.task_id)

    assert execution.success is False
    assert execution.attempts == 1
    assert execution.retry_count == 0
    assert execution.failure_type == "auth"


@pytest.mark.asyncio
async def test_timeout_retries_respect_type_policy(monkeypatch):
    manager = PersistentTaskManager(store=InMemoryStore(), user_id="u_timeout")
    task = await manager.create_task(
        goal="Network heavy task",
        trigger_type=TriggerType.MANUAL,
        max_retries=5,
    )

    calls = {"n": 0}

    async def fake_run_once(*, task, request_id):
        calls["n"] += 1
        return {"success": False, "error": "request timeout"}

    monkeypatch.setattr(manager, "_run_task_once", fake_run_once)
    monkeypatch.setattr(manager, "_retry_backoff_seconds", lambda *args, **kwargs: 0.0)

    execution = await manager.execute_task(task.task_id)

    # timeout policy allows 2 retries by default -> 3 attempts total
    assert execution.success is False
    assert execution.failure_type == "timeout"
    assert execution.retry_count == 2
    assert execution.attempts == 3
    assert calls["n"] == 3
