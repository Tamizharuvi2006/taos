from __future__ import annotations

from pathlib import Path

import pytest

from taos.core.memory.memory_portability import MemoryPortabilityService
from taos.core.memory.memory_privacy import MemoryPrivacyService
from taos.core.memory.memory_retriever import MemoryRetriever
from taos.core.memory.memory_space_store import MemorySpaceStore
from taos.core.memory.user_memory_model import UserMemory
from taos.core.memory.user_memory_store import UserMemoryStore


FIRESTORE_RULES = Path("D:/agent/frontend/firestore.rules")
STORAGE_RULES = Path("D:/agent/frontend/storage.rules")


def test_firestore_rules_lock_memory_backend_only_collections() -> None:
    text = FIRESTORE_RULES.read_text(encoding="utf-8")
    required = [
        "match /memories/{memoryId=**}",
        "match /memoryImports/{importId=**}",
        "match /memorySpaces/{spaceId=**}",
        "match /userMemories/{docId=**}",
        "match /memoryAuditLogs/{docId=**}",
        "allow read, write: if false;",
    ]
    for needle in required:
        assert needle in text


def test_firestore_rules_lock_new_backend_user_subcollections() -> None:
    text = FIRESTORE_RULES.read_text(encoding="utf-8")
    for collection in [
        "profiles",
        "tasks",
        "task_executions",
        "workflows",
        "workflow_runs",
        "notifications",
        "push_tokens",
        "payment_orders",
        "payment_webhook_events",
        "audit_logs",
        "feedback",
        "agent_memory",
        "tool_stats",
        "research_profiles",
        "executions",
    ]:
        assert f"match /{collection}/{{docId=**}}" in text


def test_storage_rules_keep_user_uploads_owner_scoped() -> None:
    text = STORAGE_RULES.read_text(encoding="utf-8")
    assert "match /users/{userId}/{allPaths=**}" in text
    assert "match /uploads/{userId}/{allPaths=**}" in text
    assert "isOwner(userId) || isAdminClaim()" in text
    assert "match /{allPaths=**}" in text
    assert "allow read, write: if false;" in text


def test_user_memory_store_never_lists_or_retrieves_other_user_memory() -> None:
    store = UserMemoryStore()
    mine = store.create(UserMemory(user_id="u1", content="User u1 prefers concise answers."))
    other = store.create(UserMemory(user_id="u2", content="User u2 secret project memory."))

    assert [memory.id for memory in store.list("u1")] == [mine.id]
    assert store.get("u1", other.id) is None
    assert store.retrieve("u1", "secret project") == []
    assert store.delete("u1", other.id) is False


def test_memory_retriever_and_privacy_export_are_user_scoped() -> None:
    store = UserMemoryStore()
    store.create(UserMemory(user_id="u1", content="TAOS package route is locked."))
    store.create(UserMemory(user_id="u2", content="Private memory from user two."))

    retrieved = MemoryRetriever(store).memory_used_summary("u1", "private user two")
    exported = MemoryPrivacyService(store).export_all("u1")

    assert retrieved == []
    assert "Private memory from user two" not in str(exported)
    assert exported["memory_count"] == 1


def test_memory_portability_sessions_are_user_scoped() -> None:
    store = UserMemoryStore()
    service = MemoryPortabilityService(store)
    session = service.analyze_import("u1", "Goal: Build TAOS")

    with pytest.raises(KeyError):
        service.confirm_import("u2", session.id, [])
    with pytest.raises(KeyError):
        service.cancel_import("u2", session.id)
    assert service.history("u2") == []


def test_shared_memory_space_requires_membership() -> None:
    store = MemorySpaceStore()
    space = store.create_space("TAOS", "owner", scope="project")
    store.add_memory(space.id, "owner", "Shared project decision.")

    with pytest.raises(PermissionError):
        store.get_space(space.id, "outsider")
    assert store.list_spaces("outsider") == []
