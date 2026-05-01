"""Small sanitized security audit event helper."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any

from .trace_sanitizer import sanitize_public_trace


@dataclass(frozen=True)
class SecurityAuditEvent:
    event_type: str
    actor: str
    action: str
    allowed: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "actor": self.actor,
            "action": self.action,
            "allowed": self.allowed,
            "reason": self.reason,
            "details": sanitize_public_trace(dict(self.details or {})),
            "timestamp": self.timestamp,
        }


class SecurityAuditLogger:
    """In-process audit collector used for tests and safe structured logging."""

    def __init__(self) -> None:
        self._events: list[SecurityAuditEvent] = []

    def record(
        self,
        *,
        event_type: str,
        actor: str = "",
        action: str,
        allowed: bool,
        reason: str = "",
        details: dict[str, Any] | None = None,
    ) -> SecurityAuditEvent:
        event = SecurityAuditEvent(
            event_type=str(event_type or "security_event"),
            actor=str(actor or ""),
            action=str(action or ""),
            allowed=bool(allowed),
            reason=str(reason or ""),
            details=dict(details or {}),
        )
        self._events.append(event)
        return event

    def events(self) -> list[SecurityAuditEvent]:
        return list(self._events)
