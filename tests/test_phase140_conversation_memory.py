from __future__ import annotations

from taos.core.memory.conversation_memory_manager import ConversationMemoryManager
from taos.core.memory.memory_policy import MemoryPolicy
from taos.core.memory.project_memory import ProjectMemory, extract_project_facts
from taos.core.memory.rolling_summary import RollingSummary


def _manager() -> ConversationMemoryManager:
    return ConversationMemoryManager(
        policy=MemoryPolicy(
            recent_turn_window=10,
            long_chat_recent_turn_window=6,
            full_context_token_limit=120,
            summary_trigger_tokens=160,
            summary_trigger_turns=12,
            max_summary_tokens=800,
        )
    )


def test_last_10_turns_are_preserved_raw_for_normal_chat() -> None:
    manager = ConversationMemoryManager(policy=MemoryPolicy(full_context_token_limit=999999))
    for idx in range(12):
        manager.add_turn(f"user turn {idx}", f"assistant turn {idx}")
    recent = manager.recent_raw_messages()
    assert len(recent) == 24
    assert recent[0].content == "user turn 0"
    assert recent[-1].content == "assistant turn 11"


def test_long_chat_keeps_recent_window_and_summarizes_older_turns() -> None:
    manager = _manager()
    for idx in range(18):
        manager.add_turn(
            f"Phase {100 + idx} completed. Goal: keep project memory for TAOS. DEVELOPMENT_LOG.md updated.",
            f"Done Phase {100 + idx} complete. Next: continue memory architecture.",
        )
    recent = manager.recent_raw_messages()
    assert len(recent) == 12
    assert manager.rolling_summary.version > 0
    assert manager.rolling_summary.completed_phases
    assert "DEVELOPMENT_LOG.md" in manager.rolling_summary.as_text()


def test_summary_updates_without_losing_decisions() -> None:
    summary = RollingSummary()
    summary.update_from_messages(
        [
            type("Msg", (), {"id": "m1", "role": "user", "content": "Package-version source-of-record path is locked."})(),
            type("Msg", (), {"id": "m2", "role": "user", "content": "Research should use best-supported answer, not generic failure."})(),
        ]
    )
    text = summary.as_text()
    assert "Package-version source-of-record behavior is locked" in text
    assert "best-supported status" in text
    assert summary.evidence_message_ids == ["m1", "m2"]


def test_project_memory_extracts_stable_facts_only_from_messages() -> None:
    facts = extract_project_facts(
        "Phase 140 complete. Package-version source-of-record is locked. Do not bypass login for public-only entity intelligence.",
        source_message_id="m1",
    )
    keys = {fact.key for fact in facts}
    assert "phase:140:status" in keys
    assert "decision:package_version_source_of_record_locked" in keys
    assert "safety:public_entity_intelligence_only" in keys
    assert all(fact.source_message_ids == ("m1",) for fact in facts)


def test_retrieved_memory_is_cited_in_trace() -> None:
    manager = _manager()
    manager.add_turn("Old decision: package-version source-of-record path is locked.", "Stored.")
    for idx in range(20):
        manager.add_turn(f"filler user {idx} " * 10, f"filler assistant {idx} " * 10)
    context = manager.build_context("what did we decide about package version?", retrieval_query="package version locked")
    citations = context["trace"]["memory_used"]["retrieved_citations"]
    assert citations
    assert citations[0]["source_message_id"] == "m1"
    assert "package-version" in citations[0]["snippet"]


def test_memory_does_not_invent_facts() -> None:
    manager = _manager()
    manager.add_turn("We discussed frontend colors only.", "No project phase was mentioned.")
    context = manager.build_context("what phase is complete?")
    project_facts = context["project_memory"]["facts"]
    assert project_facts == []
    assert "Phase" not in manager.rolling_summary.as_text()


def test_user_can_ask_what_memory_was_used() -> None:
    manager = _manager()
    manager.add_turn("Phase 140 complete. DEVELOPMENT_LOG.md updated.", "Stored.")
    status = manager.answer_memory_status()
    assert "most recent messages exactly" in status
    assert "summarize older parts" in status
    assert "retrieve relevant older details" in status


def test_context_contains_all_four_memory_layers() -> None:
    manager = _manager()
    manager.add_turn("Phase 140 complete. DEVELOPMENT_LOG.md updated.", "Stored.")
    context = manager.build_context("continue")
    layers = context["trace"]["memory_used"]["memory_layers"]
    assert layers == ["recent_raw", "rolling_summary", "project_memory", "retrieval_memory"]
    assert "recent_messages" in context
    assert "rolling_summary" in context
    assert "project_memory" in context
    assert "retrieved_memories" in context
