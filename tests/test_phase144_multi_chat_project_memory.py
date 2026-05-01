from __future__ import annotations

from taos.core.memory.project_context_selector import select_project_context
from taos.core.memory.project_memory_extractor import extract_project_memory
from taos.core.memory.project_memory_model import ProjectMemoryRecord
from taos.core.memory.project_memory_store import MultiChatProjectMemoryStore


def test_project_memory_created_from_phase_update() -> None:
    record = extract_project_memory("TAOS Phase 140 complete. Next: Phase 141 context pack.", user_id="u1")
    assert record is not None
    assert record.project_id == "taos"
    assert record.current_phase == "Phase 140"
    assert "140" in record.completed_phases


def test_completed_phases_are_merged_without_duplicates() -> None:
    store = MultiChatProjectMemoryStore()
    store.upsert(ProjectMemoryRecord(project_id="taos", project_name="TAOS", user_id="u1", completed_phases=["140"]))
    merged = store.upsert(ProjectMemoryRecord(project_id="taos", project_name="TAOS", user_id="u1", completed_phases=["140", "141"]))
    assert merged.completed_phases == ["140", "141"]


def test_continue_project_retrieves_project_memory() -> None:
    store = MultiChatProjectMemoryStore()
    store.upsert(ProjectMemoryRecord(project_id="taos", project_name="TAOS", user_id="u1", current_phase="Phase 141"))
    selected = select_project_context(store, user_id="u1", query="continue TAOS")
    assert selected.record is not None
    assert selected.confidence >= 0.8


def test_cross_project_separation() -> None:
    store = MultiChatProjectMemoryStore()
    store.upsert(ProjectMemoryRecord(project_id="taos", project_name="TAOS", user_id="u1", current_phase="Phase 141"))
    store.upsert(ProjectMemoryRecord(project_id="nyx", project_name="NYX", user_id="u1", current_phase="Phase 3"))
    assert store.get("u1", "taos").current_phase == "Phase 141"
    assert store.get("u1", "nyx").current_phase == "Phase 3"


def test_stale_project_memory_is_flagged() -> None:
    store = MultiChatProjectMemoryStore()
    store.upsert(ProjectMemoryRecord(project_id="taos", project_name="TAOS", user_id="u1", last_updated="2020-01-01T00:00:00+00:00"))
    selected = select_project_context(store, user_id="u1", query="continue TAOS")
    assert selected.stale is True
