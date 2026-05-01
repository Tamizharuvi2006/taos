from __future__ import annotations

from taos.core.memory.memory_extractor import build_memory_used_summary, extract_memories_from_text, summarize_visible_memories
from taos.core.memory.memory_policy import should_save_user_memory
from taos.core.memory.user_memory_model import UserMemory
from taos.core.memory.user_memory_store import UserMemoryStore


def test_create_list_edit_delete_and_delete_all_memory() -> None:
    store = UserMemoryStore()
    memory = store.create(UserMemory(user_id="u1", content="User prefers concise Tanglish mentor style."))
    assert store.list("u1")[0].id == memory.id

    updated = store.update("u1", memory.id, content="User prefers concise answers.")
    assert updated.content == "User prefers concise answers."

    assert store.delete("u1", memory.id) is True
    assert store.list("u1") == []

    store.create(UserMemory(user_id="u1", content="Project is TAOS."))
    store.create(UserMemory(user_id="u1", content="Use best-supported answers."))
    assert store.delete_all("u1") == 2
    assert store.list("u1") == []


def test_explicit_remember_request_saves_memory() -> None:
    extraction = extract_memories_from_text("remember that I prefer concise answers", user_id="u1")
    assert extraction.action == "save"
    assert extraction.memories[0].content == "I prefer concise answers"
    assert extraction.memories[0].reason_saved.startswith("User explicitly")


def test_secret_api_key_is_not_saved() -> None:
    decision = should_save_user_memory("remember my API key is sk-secret123456")
    extraction = extract_memories_from_text("remember my API key is sk-secret123456", user_id="u1")
    assert decision.should_save is False
    assert extraction.action == "blocked"


def test_disabled_memory_is_not_retrieved() -> None:
    store = UserMemoryStore()
    memory = store.create(UserMemory(user_id="u1", content="User prefers concise answers."))
    store.update("u1", memory.id, status="disabled")
    assert store.retrieve("u1", "concise answers") == []


def test_memory_used_summary_and_what_do_you_remember() -> None:
    store = UserMemoryStore()
    memory = store.create(UserMemory(user_id="u1", content="User prefers Tanglish mentor tone.", tags=["tone"]))
    used = build_memory_used_summary(store.retrieve("u1", "tone"))
    assert used[0]["content"] == memory.content
    summary = summarize_visible_memories(store.list("u1"))
    assert "Tanglish mentor tone" in summary


def test_memory_settings_toggle_off_blocks_retrieval() -> None:
    store = UserMemoryStore()
    store.create(UserMemory(user_id="u1", content="User prefers concise answers."))
    store.update_settings("u1", memory_enabled=False)
    assert store.retrieve("u1", "concise") == []
