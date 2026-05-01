"""
TAOS Tests - Persistence Layer.

Tests InMemoryStore and PersistentTaskManager.
Firebase tests are skipped unless firebase-admin is installed.
"""

from __future__ import annotations

import asyncio

import pytest

from taos.apps.api.routes.chats import ChatMessageInput
from taos.core.tasks.persistent_manager import PersistentTaskManager
from taos.infra.persistence.store import InMemoryStore


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestInMemoryStore:
    @pytest.fixture
    def store(self):
        return InMemoryStore()

    def test_set_and_get(self, store):
        _run_async(store.set("tasks", "t1", {"goal": "test"}))
        result = _run_async(store.get("tasks", "t1"))
        assert result is not None
        assert result["goal"] == "test"

    def test_get_missing(self, store):
        result = _run_async(store.get("tasks", "nope"))
        assert result is None

    def test_delete(self, store):
        _run_async(store.set("tasks", "t1", {"goal": "test"}))
        result = _run_async(store.delete("tasks", "t1"))
        assert result is True
        assert _run_async(store.get("tasks", "t1")) is None

    def test_list(self, store):
        _run_async(store.set("tasks", "t1", {"status": "active"}))
        _run_async(store.set("tasks", "t2", {"status": "paused"}))
        docs = _run_async(store.list("tasks"))
        assert len(docs) == 2

    def test_list_with_filter(self, store):
        _run_async(store.set("tasks", "t1", {"status": "active"}))
        _run_async(store.set("tasks", "t2", {"status": "paused"}))
        docs = _run_async(store.list("tasks", filters={"status": "active"}))
        assert len(docs) == 1

    def test_update(self, store):
        _run_async(store.set("tasks", "t1", {"status": "active", "name": "A"}))
        _run_async(store.update("tasks", "t1", {"status": "paused"}))
        doc = _run_async(store.get("tasks", "t1"))
        assert doc["status"] == "paused"
        assert doc["name"] == "A"

    def test_user_scoping(self, store):
        _run_async(store.set("tasks", "t1", {"goal": "a"}, user_id="user1"))
        _run_async(store.set("tasks", "t1", {"goal": "b"}, user_id="user2"))
        r1 = _run_async(store.get("tasks", "t1", user_id="user1"))
        r2 = _run_async(store.get("tasks", "t1", user_id="user2"))
        assert r1["goal"] == "a"
        assert r2["goal"] == "b"

    def test_append_to_list(self, store):
        _run_async(store.set("tasks", "t1", {"history": []}))
        _run_async(store.append_to_list("tasks", "t1", "history", {"exec": 1}))
        doc = _run_async(store.get("tasks", "t1"))
        assert len(doc["history"]) == 1

    def test_chat_message_input_preserves_trace_metadata(self):
        msg = ChatMessageInput.model_validate(
            {
                "id": "m1",
                "role": "assistant",
                "text": "Answer",
                "status": "done",
                "ts": 1.0,
                "trace": {"request_id": "req_1"},
                "trust_block": {"evidence": "Strong"},
            }
        )
        dumped = msg.model_dump()
        assert dumped["trace"]["request_id"] == "req_1"
        assert dumped["trust_block"]["evidence"] == "Strong"


class TestPersistentTaskManager:
    @pytest.fixture
    def manager(self):
        store = InMemoryStore()
        return PersistentTaskManager(store=store, user_id="test-user")

    def test_create_task(self, manager):
        task = _run_async(manager.create_task(goal="Check Python version"))
        assert task.task_id.startswith("task_")
        assert task.goal == "Check Python version"

    def test_get_task(self, manager):
        task = _run_async(manager.create_task(goal="test"))
        found = _run_async(manager.get_task(task.task_id))
        assert found is not None
        assert found.goal == "test"

    def test_list_tasks(self, manager):
        _run_async(manager.create_task(goal="t1"))
        _run_async(manager.create_task(goal="t2"))
        tasks = _run_async(manager.list_tasks())
        assert len(tasks) == 2

    def test_delete_task(self, manager):
        task = _run_async(manager.create_task(goal="delete me"))
        result = _run_async(manager.delete_task(task.task_id))
        assert result is True

    def test_stats(self, manager):
        _run_async(manager.create_task(goal="t1"))
        stats = _run_async(manager.stats())
        assert stats["total_tasks"] == 1
        assert stats["user_id"] == "test-user"

    def test_user_isolation(self):
        store = InMemoryStore()
        m1 = PersistentTaskManager(store=store, user_id="alice")
        m2 = PersistentTaskManager(store=store, user_id="bob")

        _run_async(m1.create_task(goal="alice task"))
        _run_async(m2.create_task(goal="bob task"))

        alice_tasks = _run_async(m1.list_tasks())
        bob_tasks = _run_async(m2.list_tasks())

        assert len(alice_tasks) == 1
        assert len(bob_tasks) == 1
        assert alice_tasks[0].goal == "alice task"
        assert bob_tasks[0].goal == "bob task"
