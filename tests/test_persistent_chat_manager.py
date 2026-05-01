from __future__ import annotations

import pytest

from taos.core.chat.persistent_chat_manager import CHATS_COLLECTION, PersistentChatManager
from taos.infra.persistence.store import InMemoryStore


class CountingStore(InMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0
        self.set_calls = 0
        self.delete_calls = 0

    async def get(self, collection: str, doc_id: str, user_id: str = "default"):
        self.get_calls += 1
        return await super().get(collection=collection, doc_id=doc_id, user_id=user_id)

    async def set(self, collection: str, doc_id: str, data, user_id: str = "default"):
        self.set_calls += 1
        await super().set(collection=collection, doc_id=doc_id, data=data, user_id=user_id)

    async def delete(self, collection: str, doc_id: str, user_id: str = "default"):
        self.delete_calls += 1
        return await super().delete(collection=collection, doc_id=doc_id, user_id=user_id)


@pytest.mark.asyncio
async def test_upsert_chat_skips_noop_persistence_writes():
    store = CountingStore()
    manager = PersistentChatManager(user_id="u1", store=store)
    payload = {
        "title": "My Chat",
        "messages": [{"id": "m1", "role": "user", "text": "hello", "status": "done", "ts": 1.0}],
        "doc_ids": [],
    }

    first = await manager.upsert_chat("c1", payload)
    second = await manager.upsert_chat("c1", payload)

    assert first["id"] == "c1"
    assert second == first
    assert store.get_calls == 1
    assert store.set_calls == 1


@pytest.mark.asyncio
async def test_upsert_chat_persists_when_content_changes():
    store = CountingStore()
    manager = PersistentChatManager(user_id="u1", store=store)
    payload = {
        "title": "My Chat",
        "messages": [{"id": "m1", "role": "user", "text": "hello", "status": "done", "ts": 1.0}],
        "doc_ids": [],
    }

    first = await manager.upsert_chat("c1", payload)
    changed_payload = {
        **payload,
        "messages": [
            payload["messages"][0],
            {"id": "m2", "role": "assistant", "text": "hi", "status": "done", "ts": 2.0},
        ],
    }
    second = await manager.upsert_chat("c1", changed_payload)

    assert second["messages"] != first["messages"]
    assert second["updated_at"] >= first["updated_at"]
    assert store.set_calls == 2


@pytest.mark.asyncio
async def test_delete_chat_clears_local_cache():
    store = CountingStore()
    manager = PersistentChatManager(user_id="u1", store=store)
    payload = {
        "title": "My Chat",
        "messages": [{"id": "m1", "role": "user", "text": "hello", "status": "done", "ts": 1.0}],
        "doc_ids": [],
    }

    await manager.upsert_chat("c1", payload)
    deleted = await manager.delete_chat("c1")

    assert deleted is True
    assert "c1" not in manager._chat_cache  # internal cache contract for no-op optimization
    assert "c1" not in manager._signature_cache
    assert store.delete_calls == 1
    assert await store.get(CHATS_COLLECTION, "c1", user_id="u1") is None
