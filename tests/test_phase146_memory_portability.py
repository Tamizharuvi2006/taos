from __future__ import annotations

from taos.core.memory.memory_import_analyzer import analyze_memory_import
from taos.core.memory.memory_portability import ImportConfirmSelection, MemoryPortabilityService
from taos.core.memory.user_memory_model import UserMemory
from taos.core.memory.user_memory_store import UserMemoryStore


def _service() -> tuple[MemoryPortabilityService, UserMemoryStore]:
    store = UserMemoryStore()
    return MemoryPortabilityService(store), store


def test_export_excludes_deleted_disabled_and_sensitive_memories() -> None:
    service, store = _service()
    active = store.create(UserMemory(user_id="u1", content="Assistant style: friendly Tanglish mentor.", tags=["assistant_style"]))
    disabled = store.create(UserMemory(user_id="u1", content="Disabled memory", status="disabled"))
    deleted = store.create(UserMemory(user_id="u1", content="Deleted memory"))
    secret = store.create(UserMemory(user_id="u1", content="API key is sk-secret123456"))
    store.delete("u1", deleted.id)

    exported = service.export_profile("u1", preferred_name="Tamizh")

    assert "friendly Tanglish mentor" in exported["text"]
    assert "Tamizh" in exported["text"]
    assert "Disabled memory" not in exported["text"]
    assert "Deleted memory" not in exported["text"]
    assert "sk-secret" not in exported["text"]
    assert exported["memory_count"] == 1


def test_export_includes_assistant_style_and_project_context() -> None:
    service, store = _service()
    store.create(UserMemory(user_id="u1", type="saved_memory", content="User prefers concise Tanglish mentor style.", tags=["assistant_style"]))
    store.create(UserMemory(user_id="u1", type="project_memory", content="TAOS current focus is memory portability.", tags=["project_context"]))

    exported = service.export_profile("u1")

    assert "Preferred assistant style" in exported["text"]
    assert "Current project" in exported["text"]
    assert "memory portability" in exported["text"]
    assert exported["json"]["sections"]["assistant_style"]


def test_import_analyze_extracts_candidate_memories() -> None:
    session = analyze_memory_import(
        """
        Preferred assistant style: Friendly Tanglish mentor vibe.
        Goal: Become job-ready Full Stack + AI Engineer.
        Current project: TAOS AgentOS memory system.
        """,
        user_id="u1",
    )

    types = {item.type for item in session.items}
    assert {"assistant_style", "career_goal", "project_context"}.issubset(types)
    assert all(item.recommended for item in session.items)
    assert session.id.startswith("imp_")


def test_import_analyze_blocks_api_keys_and_secrets() -> None:
    session = analyze_memory_import("Remember API key is sk-abc123456789", user_id="u1")
    assert session.items[0].type == "blocked_sensitive"
    assert session.items[0].content == "[REDACTED BLOCKED MEMORY]"
    assert session.items[0].recommended is False


def test_import_confirm_saves_only_selected_items_and_allows_edit_before_save() -> None:
    service, store = _service()
    session = service.analyze_import("u1", "Tone: concise mentor.\nProject: TAOS memory system.")
    selected = [
        ImportConfirmSelection(
            item_id=session.items[0].id,
            selected=True,
            content="Assistant style: concise technical mentor.",
            type="assistant_style",
        ),
        ImportConfirmSelection(item_id=session.items[1].id, selected=False),
    ]

    result = service.confirm_import("u1", session.id, selected)
    memories = store.list("u1")

    assert result["saved_count"] == 1
    assert len(memories) == 1
    assert memories[0].content == "Assistant style: concise technical mentor."
    assert memories[0].reason_saved == "Imported by user after review"


def test_import_cancel_saves_nothing() -> None:
    service, store = _service()
    session = service.analyze_import("u1", "Goal: Build production AI apps.")
    result = service.cancel_import("u1", session.id)

    assert result["status"] == "cancelled"
    assert store.list("u1") == []


def test_sensitive_memory_requires_explicit_confirmation() -> None:
    service, store = _service()
    session = service.analyze_import("u1", "Medical diagnosis: migraine history.")
    item = session.items[0]
    assert item.risk == "sensitive"
    blocked = service.confirm_import("u1", session.id, [ImportConfirmSelection(item_id=item.id, selected=True)])
    assert blocked["saved_count"] == 0
    assert blocked["blocked"]

    session2 = service.analyze_import("u1", "Medical diagnosis: migraine history.")
    saved = service.confirm_import(
        "u1",
        session2.id,
        [ImportConfirmSelection(item_id=session2.items[0].id, selected=True, allow_sensitive=True)],
    )
    assert saved["saved_count"] == 1
    assert store.list("u1")[0].content == "Medical diagnosis: migraine history."


def test_imported_memories_appear_in_memory_store_and_history() -> None:
    service, store = _service()
    session = service.analyze_import("u1", "Goal: Become job-ready AI engineer.")
    service.confirm_import("u1", session.id, [ImportConfirmSelection(item_id=session.items[0].id, selected=True)])

    listed = store.list("u1")
    history = service.history("u1")

    assert listed
    assert listed[0].tags == ["career_goal", "imported"]
    assert any(entry["status"] == "confirmed" for entry in history)
