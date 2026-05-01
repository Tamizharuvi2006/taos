from __future__ import annotations

from taos.core.memory.memory_privacy import MemoryPrivacyService
from taos.core.memory.user_memory_model import UserMemory
from taos.core.memory.user_memory_store import UserMemoryStore


def test_export_all_active_memories_and_excludes_deleted() -> None:
    store = UserMemoryStore()
    service = MemoryPrivacyService(store)
    store.create(UserMemory(user_id="u1", content="Active memory"))
    deleted = store.create(UserMemory(user_id="u1", content="Deleted memory"))
    store.delete("u1", deleted.id)
    bundle = service.export_all("u1")
    assert bundle["memory_count"] == 1
    assert "Active memory" in bundle["ai_profile_text"]
    assert "Deleted memory" not in bundle["ai_profile_text"]


def test_delete_all_disables_recall() -> None:
    store = UserMemoryStore()
    service = MemoryPrivacyService(store)
    store.create(UserMemory(user_id="u1", content="Recall me"))
    assert service.delete_all("u1")["deleted"] == 1
    assert store.retrieve("u1", "Recall") == []


def test_delete_imported_only_affects_imported_memories() -> None:
    store = UserMemoryStore()
    service = MemoryPrivacyService(store)
    store.create(UserMemory(user_id="u1", content="Imported", tags=["imported"]))
    store.create(UserMemory(user_id="u1", content="Manual"))
    assert service.delete_imported("u1")["deleted"] == 1
    assert [memory.content for memory in store.list("u1")] == ["Manual"]


def test_memory_off_prevents_retrieval() -> None:
    store = UserMemoryStore()
    service = MemoryPrivacyService(store)
    store.create(UserMemory(user_id="u1", content="User prefers concise answers."))
    service.update_settings("u1", memory_enabled=False)
    assert service.retrievable_memories("u1", "concise") == []


def test_reference_chat_history_off_excludes_chat_summaries() -> None:
    store = UserMemoryStore()
    service = MemoryPrivacyService(store)
    store.create(UserMemory(user_id="u1", type="chat_summary", content="Old chat summary about TAOS."))
    service.update_settings("u1", reference_chat_history=False)
    assert service.retrievable_memories("u1", "TAOS") == []


def test_audit_log_safe_and_hard_delete_removes_content() -> None:
    store = UserMemoryStore()
    service = MemoryPrivacyService(store)
    memory = store.create(UserMemory(user_id="u1", content="Hard delete me"))
    service.delete_all("u1", hard=True)
    assert store.get("u1", memory.id) is None
    events = service.audit_log("u1")
    assert events[-1]["action"] == "delete_all"
    assert "Hard delete me" not in str(events)
