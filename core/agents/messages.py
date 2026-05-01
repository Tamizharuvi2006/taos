"""Agent communication protocol and in-memory message bus."""

from __future__ import annotations

import hashlib
import time
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    INFO = "info"
    REQUEST = "request"
    WARNING = "warning"
    ERROR = "error"
    SUGGESTION = "suggestion"
    DECISION = "decision"


class MessagePriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AgentMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: float = Field(default_factory=time.time)
    agent_name: str
    step_id: str = ""
    message_type: MessageType
    priority: MessagePriority = MessagePriority.MEDIUM
    content: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    targets: List[str] = Field(default_factory=list)
    ttl_steps: int = 3
    ttl_seconds: int = 600
    created_step_index: int = 0


class AgentMessageStore:
    """Bounded, deduplicated message bus with TTL cleanup."""

    def __init__(
        self,
        max_messages_per_step: int = 5,
        max_total_messages: int = 200,
    ) -> None:
        self._messages: List[AgentMessage] = []
        self._max_messages_per_step = max_messages_per_step
        self._max_total_messages = max_total_messages
        self._dedup_keys: Dict[str, float] = {}

    def clear(self) -> None:
        self._messages.clear()
        self._dedup_keys.clear()

    def emit(
        self,
        message: AgentMessage,
        current_step_index: int,
    ) -> bool:
        """
        Add a message if it passes dedup + per-step cap checks.
        Returns True if accepted.
        """
        self.cleanup(current_step_index)
        step_count = sum(1 for m in self._messages if m.created_step_index == current_step_index)
        if step_count >= self._max_messages_per_step:
            return False

        dedup_key = self._build_dedup_key(message)
        if dedup_key in self._dedup_keys:
            return False

        self._messages.append(message)
        self._dedup_keys[dedup_key] = time.time()

        if len(self._messages) > self._max_total_messages:
            self._messages = self._messages[-self._max_total_messages :]
            self._rebuild_dedup()

        return True

    def active(
        self,
        current_step_index: int,
        target_agent: Optional[str] = None,
    ) -> List[AgentMessage]:
        self.cleanup(current_step_index)
        result = self._messages
        if target_agent:
            result = [
                m for m in result
                if not m.targets or target_agent in m.targets
            ]
        return list(result)

    def cleanup(self, current_step_index: int) -> None:
        now = time.time()
        kept: List[AgentMessage] = []
        for msg in self._messages:
            age_sec = now - msg.timestamp
            age_steps = current_step_index - msg.created_step_index
            if age_sec <= msg.ttl_seconds and age_steps <= msg.ttl_steps:
                kept.append(msg)
        self._messages = kept
        self._rebuild_dedup()

    def _build_dedup_key(self, message: AgentMessage) -> str:
        base = (
            f"{message.agent_name}|{message.step_id}|{message.message_type.value}|"
            f"{message.priority.value}|{message.content.strip().lower()}"
        )
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:20]

    def _rebuild_dedup(self) -> None:
        self._dedup_keys = {self._build_dedup_key(m): m.timestamp for m in self._messages}
