from __future__ import annotations

from taos.core.memory.memory_conflict_detector import detect_memory_conflicts, filter_unresolved_conflicted_memories
from taos.core.memory.memory_conflict_resolver import MemoryConflictResolver
from taos.core.memory.user_memory_model import UserMemory
from taos.core.memory.user_memory_store import UserMemoryStore


def test_detects_direct_preference_conflict() -> None:
    old = UserMemory(user_id="u1", content="User prefers short answers.")
    new = UserMemory(user_id="u1", content="User prefers detailed answers.")
    conflicts = detect_memory_conflicts("u1", [old], [new])
    assert conflicts[0].type == "preference_conflict"


def test_detects_duplicate_memory() -> None:
    conflicts = detect_memory_conflicts(
        "u1",
        [UserMemory(user_id="u1", content="User prefers Tanglish mentor style.")],
        [UserMemory(user_id="u1", content="User prefers Tanglish mentor style.")],
    )
    assert conflicts[0].type == "duplicate_memory"


def test_detects_stale_project_phase() -> None:
    conflicts = detect_memory_conflicts(
        "u1",
        [UserMemory(user_id="u1", content="TAOS current phase is Phase 140.")],
        [UserMemory(user_id="u1", content="TAOS current phase is Phase 147.")],
    )
    assert conflicts[0].type == "stale_memory"


def test_merge_creates_new_memory_and_disables_old_ones() -> None:
    store = UserMemoryStore()
    old = store.create(UserMemory(user_id="u1", content="User prefers short answers."))
    new = store.create(UserMemory(user_id="u1", content="User prefers detailed phase plans."))
    resolver = MemoryConflictResolver(store)
    conflict = resolver.detect("u1", [new])[0]
    result = resolver.resolve("u1", conflict.id, "merge", merged_content="Detailed phase plans for projects, concise for simple answers.")
    assert result["created_memory"]["content"].startswith("Detailed phase plans")
    assert store.get("u1", old.id).status == "disabled"
    assert store.get("u1", new.id).status == "disabled"


def test_keep_new_disables_old_and_keep_old_disables_new() -> None:
    store = UserMemoryStore()
    old = store.create(UserMemory(user_id="u1", content="User prefers short answers."))
    new = store.create(UserMemory(user_id="u1", content="User prefers detailed answers."))
    resolver = MemoryConflictResolver(store)
    conflict = resolver.detect("u1", [new])[0]
    resolver.resolve("u1", conflict.id, "keep_new")
    assert store.get("u1", old.id).status == "disabled"


def test_unresolved_conflicts_excluded_from_context_candidates() -> None:
    old = UserMemory(user_id="u1", content="User prefers short answers.")
    new = UserMemory(user_id="u1", content="User prefers detailed answers.")
    conflict = detect_memory_conflicts("u1", [old], [new])[0]
    assert filter_unresolved_conflicted_memories([old, new], [conflict]) == []
