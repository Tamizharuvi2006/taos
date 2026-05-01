from __future__ import annotations

import pytest

from taos.core.memory.memory_attribution import cautious_memory_phrase
from taos.core.memory.memory_extractor import extract_memories_from_text
from taos.core.memory.memory_safety import MemorySafetyGuard
from taos.core.memory.user_memory_model import UserMemory
from taos.core.memory.user_memory_store import UserMemoryStore


def test_no_source_cannot_claim_saved_memory() -> None:
    memory = UserMemory(user_id="u1", content="User likes React.", reason_saved="")
    check = MemorySafetyGuard().verify_claim("User likes React", [memory])
    assert check.allowed is False


def test_deleted_disabled_and_cross_user_memories_not_usable() -> None:
    guard = MemorySafetyGuard()
    memories = [
        UserMemory(user_id="u1", content="Active"),
        UserMemory(user_id="u1", content="Deleted", status="deleted"),
        UserMemory(user_id="u1", content="Disabled", status="disabled"),
        UserMemory(user_id="u2", content="Other user"),
    ]
    usable = guard.usable_memories(memories, user_id="u1")
    assert [memory.content for memory in usable] == ["Active"]
    with pytest.raises(PermissionError):
        guard.assert_user_scope(memories[-1], "u1")


def test_api_key_and_sensitive_data_are_blocked() -> None:
    guard = MemorySafetyGuard()
    assert guard.can_store("remember my API key is sk-test123456").allowed is False
    assert guard.can_store("my passport number is X", explicit_user_request=False).allowed is False


def test_low_confidence_memory_is_cautious() -> None:
    memory = UserMemory(user_id="u1", content="User may prefer short answers.", confidence=0.4)
    assert cautious_memory_phrase(memory).startswith("I have a low-confidence")


def test_forget_command_prevents_future_recall() -> None:
    store = UserMemoryStore()
    memory = store.create(UserMemory(user_id="u1", content="User prefers concise answers."))
    forget = extract_memories_from_text("forget concise answers", user_id="u1")
    matches = store.retrieve("u1", forget.forget_query)
    for match in matches:
        store.delete("u1", match.id)
    assert store.retrieve("u1", "concise answers") == []
    assert store.get("u1", memory.id).status == "deleted"
