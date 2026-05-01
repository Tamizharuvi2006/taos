"""Streaming event contracts for real-time collaboration UX."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from taos.core.performance.progress import ProgressPhase


class StreamEventType(str, Enum):
    START = "START"
    PROGRESS = "PROGRESS"
    TOKEN = "TOKEN"
    STEP_EXECUTED = "STEP_EXECUTED"
    AGENT_SELECTED = "AGENT_SELECTED"
    DEBATE_STARTED = "DEBATE_STARTED"
    DEBATE_RESULT = "DEBATE_RESULT"
    FINAL = "FINAL"
    ERROR = "ERROR"
    PING = "PING"


class StreamEvent(BaseModel):
    request_id: str
    event_type: StreamEventType
    phase_name: str = ""
    progress: int = 0
    message: str = ""
    partial_result: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)

    def to_sse_payload(self) -> Dict[str, Any]:
        base = {
            "request_id": self.request_id,
            "event_type": self.event_type.value,
            "phase_name": self.phase_name,
            "progress": self.progress,
            "message": self.message,
            "timestamp": self.timestamp,
        }
        if self.partial_result:
            base["partial_result"] = self.partial_result
        if self.payload:
            base["payload"] = self.payload
        return base


def phase_to_event_type(phase: str, detail: str = "") -> StreamEventType:
    d = (detail or "").upper()
    if d.startswith("AGENT_SELECTED"):
        return StreamEventType.AGENT_SELECTED
    if d.startswith("DEBATE_STARTED"):
        return StreamEventType.DEBATE_STARTED
    if d.startswith("DEBATE_RESULT"):
        return StreamEventType.DEBATE_RESULT
    if d.startswith("TOKEN"):
        return StreamEventType.TOKEN
    if d.startswith("PROGRESS"):
        return StreamEventType.PROGRESS
    if d.startswith("STEP_EXECUTED"):
        return StreamEventType.STEP_EXECUTED
    mapping = {
        ProgressPhase.RECEIVED.value: StreamEventType.START,
        ProgressPhase.EXECUTING.value: StreamEventType.PROGRESS,
        ProgressPhase.COMPLETE.value: StreamEventType.FINAL,
        ProgressPhase.FAILED.value: StreamEventType.ERROR,
    }
    return mapping.get(phase, StreamEventType.PROGRESS)
