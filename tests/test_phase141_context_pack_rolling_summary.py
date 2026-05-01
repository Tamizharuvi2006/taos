from __future__ import annotations

from taos.core.memory.context_pack import ContextPackBuilder
from taos.core.memory.conversation_memory_manager import ConversationMessage
from taos.core.memory.conversation_window import select_recent_messages
from taos.core.memory.rolling_summary import RollingSummary
from taos.core.memory.user_memory_model import UserMemory


def _messages(count: int, suffix: str = ""):
    return [ConversationMessage(id=f"m{i}", role="user" if i % 2 else "assistant", content=f"message {i} {suffix}") for i in range(count)]


def test_short_chat_keeps_full_raw_context() -> None:
    rows = _messages(6)
    assert len(select_recent_messages(rows, full_context_token_limit=99999)) == 6


def test_normal_chat_keeps_last_10_turns() -> None:
    rows = _messages(30, "x" * 100)
    recent = select_recent_messages(rows, full_context_token_limit=10, long_chat_token_threshold=999999, normal_turns=10)
    assert len(recent) == 20
    assert recent[0].id == "m10"


def test_long_code_heavy_chat_keeps_last_6_turns() -> None:
    rows = _messages(30, "code " * 500)
    recent = select_recent_messages(rows, full_context_token_limit=10, long_chat_token_threshold=20, long_chat_turns=6)
    assert len(recent) == 12
    assert recent[0].id == "m18"


def test_context_pack_includes_summary_memory_and_trace() -> None:
    rows = _messages(4)
    summary = RollingSummary()
    summary.update_from_messages([ConversationMessage(id="old", role="user", content="Package-version source-of-record path is locked.")])
    memory = UserMemory(user_id="u1", content="User prefers concise answers.")
    pack = ContextPackBuilder().build(
        user_id="u1",
        current_user_message="continue TAOS",
        messages=rows,
        rolling_summary=summary,
        saved_memories=[memory],
    )
    assert pack.trace["context_summary"]["recent_turns_count"] == 2
    assert pack.trace["context_summary"]["rolling_summary_used"] is True
    assert pack.trace["memory_used_summary"][0]["content"] == "User prefers concise answers."


def test_disabled_memory_excluded_from_context_pack() -> None:
    memory = UserMemory(user_id="u1", content="Disabled", status="disabled")
    pack = ContextPackBuilder().build(
        user_id="u1",
        current_user_message="continue",
        messages=_messages(2),
        saved_memories=[memory],
    )
    assert pack.saved_memories == []
