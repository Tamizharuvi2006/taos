from __future__ import annotations

from taos.core.memory.memory_retriever import MemoryRetriever
from taos.core.memory.recall_query_builder import build_recall_query
from taos.core.memory.user_memory_model import UserMemory
from taos.core.memory.user_memory_store import UserMemoryStore


def test_recall_query_detects_explicit_memory_request() -> None:
    recall = build_recall_query("what did we decide about TAOS search lite?")
    assert recall.explicit_recall is True
    assert "taos search lite" in recall.query


def test_recall_retrieves_saved_and_project_memory() -> None:
    store = UserMemoryStore()
    store.create(UserMemory(user_id="u1", type="saved_memory", content="User prefers concise answers."))
    store.create(UserMemory(user_id="u1", type="project_memory", content="TAOS Phase 140 is memory center."))
    used = MemoryRetriever(store).memory_used_summary("u1", "what do you remember about TAOS phase")
    assert used
    assert used[0]["type"] == "project_memory"


def test_deleted_and_disabled_memories_are_excluded() -> None:
    store = UserMemoryStore()
    deleted = store.create(UserMemory(user_id="u1", content="Deleted memory about vite."))
    disabled = store.create(UserMemory(user_id="u1", content="Disabled memory about vite."))
    store.delete("u1", deleted.id)
    store.update("u1", disabled.id, status="disabled")
    assert MemoryRetriever(store).memory_used_summary("u1", "vite") == []


def test_memory_used_summary_records_ids_types_and_reasons() -> None:
    store = UserMemoryStore()
    memory = store.create(UserMemory(user_id="u1", content="Package-version source-of-record is locked."))
    used = MemoryRetriever(store).memory_used_summary("u1", "source of record package")
    assert used[0]["id"] == memory.id
    assert used[0]["reason_used"].startswith("lexical_overlap")
    assert used[0]["attribution"]["reason_saved"] == memory.reason_saved
