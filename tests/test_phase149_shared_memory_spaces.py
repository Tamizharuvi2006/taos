from __future__ import annotations

import pytest

from taos.core.memory.memory_space_store import MemorySpaceStore
from taos.core.memory.project_shared_memory import shared_memory_for_context


def test_create_space_and_add_memory() -> None:
    store = MemorySpaceStore()
    space = store.create_space("TAOS", "owner", scope="project")
    memory = store.add_memory(space.id, "owner", "Package-version lookup stays source-of-record.")
    assert memory.content.startswith("Package-version")
    assert store.get_space(space.id, "owner").memories[memory.id].content == memory.content


def test_viewer_can_read_but_not_edit() -> None:
    store = MemorySpaceStore()
    space = store.create_space("TAOS", "owner")
    store.add_member(space.id, "owner", "viewer", "viewer")
    assert store.get_space(space.id, "viewer").id == space.id
    with pytest.raises(PermissionError):
        store.add_memory(space.id, "viewer", "No edit")


def test_editor_can_add_and_edit() -> None:
    store = MemorySpaceStore()
    space = store.create_space("TAOS", "owner")
    store.add_member(space.id, "owner", "editor", "editor")
    memory = store.add_memory(space.id, "editor", "Old")
    updated = store.update_memory(space.id, "editor", memory.id, content="Updated")
    assert updated.content == "Updated"


def test_unauthorized_user_cannot_access() -> None:
    store = MemorySpaceStore()
    space = store.create_space("TAOS", "owner")
    with pytest.raises(PermissionError):
        store.get_space(space.id, "stranger")


def test_personal_memory_not_visible_in_team_space_and_cross_project_isolation() -> None:
    store = MemorySpaceStore()
    a = store.create_space("A", "owner")
    b = store.create_space("B", "owner")
    store.add_memory(a.id, "owner", "A memory")
    store.add_memory(b.id, "owner", "B memory")
    assert len(store.get_space(a.id, "owner").memories) == 1
    assert list(store.get_space(a.id, "owner").memories.values())[0].content == "A memory"


def test_context_pack_includes_authorized_project_memory() -> None:
    store = MemorySpaceStore()
    space = store.create_space("TAOS", "owner")
    store.add_memory(space.id, "owner", "Shared route contract.")
    context = shared_memory_for_context(store, user_id="owner", active_space_id=space.id)
    assert context["memory_space_used_summary"]["memory_count"] == 1
    assert context["shared_memories"][0]["content"] == "Shared route contract."
