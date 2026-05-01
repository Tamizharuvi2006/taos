from __future__ import annotations

from taos.core.agents.messages import (
    AgentMessage,
    AgentMessageStore,
    MessagePriority,
    MessageType,
)


def _msg(
    *,
    agent_name: str = "research_agent",
    step_id: str = "s1",
    message_type: MessageType = MessageType.INFO,
    priority: MessagePriority = MessagePriority.MEDIUM,
    content: str = "info",
    confidence: float = 0.8,
    ttl_steps: int = 3,
    created_step_index: int = 0,
) -> AgentMessage:
    return AgentMessage(
        agent_name=agent_name,
        step_id=step_id,
        message_type=message_type,
        priority=priority,
        content=content,
        confidence=confidence,
        ttl_steps=ttl_steps,
        ttl_seconds=1000,
        created_step_index=created_step_index,
    )


def test_message_store_dedup_and_step_cap():
    store = AgentMessageStore(max_messages_per_step=2, max_total_messages=10)
    assert store.emit(_msg(content="A"), current_step_index=0) is True
    assert store.emit(_msg(content="A"), current_step_index=0) is False  # dedup
    assert store.emit(_msg(content="B"), current_step_index=0) is True
    assert store.emit(_msg(content="C"), current_step_index=0) is False  # cap reached


def test_message_store_ttl_cleanup_by_step():
    store = AgentMessageStore(max_messages_per_step=5, max_total_messages=10)
    store.emit(_msg(content="old", ttl_steps=1, created_step_index=0), current_step_index=0)
    assert len(store.active(current_step_index=0)) == 1
    assert len(store.active(current_step_index=2)) == 0


def test_message_store_targets_filter():
    store = AgentMessageStore()
    msg = _msg(content="need research")
    msg.targets = ["research_agent"]
    store.emit(msg, current_step_index=0)
    assert len(store.active(current_step_index=0, target_agent="research_agent")) == 1
    assert len(store.active(current_step_index=0, target_agent="critic_agent")) == 0
