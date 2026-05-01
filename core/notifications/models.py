"""
TAOS Notifications — Models.

Defines the structure for Webhook and Email notifications.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class NotificationChannel(str, Enum):
    """Supported notification channels."""
    WEBHOOK = "webhook"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    FCM = "fcm"
    SMS = "sms"  # For future Twilio integration


class NotificationEvent(str, Enum):
    """Events that trigger a notification."""
    TASK_SUCCESS = "task_success"
    TASK_FAILED = "task_failed"
    CONDITION_MET = "condition_met"
    ALWAYS = "always"


@dataclass
class NotificationConfig:
    """
    Configuration for a single notification target.

    Example (Webhook):
        channel: WEBHOOK
        target: https://api.example.com/webhook
        events: [CONDITION_MET]

    Example (Email):
        channel: EMAIL
        target: user@example.com
        events: [TASK_FAILED]
    """

    channel: NotificationChannel
    target: str  # URL for webhook, Email address for email, Phone number for SMS
    events: List[NotificationEvent] = field(default_factory=lambda: [NotificationEvent.CONDITION_MET])
    headers: Dict[str, str] = field(default_factory=dict)  # Only used for webhooks

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel.value,
            "target": self.target,
            "events": [e.value for e in self.events],
            "headers": self.headers,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NotificationConfig:
        return cls(
            channel=NotificationChannel(data.get("channel", "webhook")),
            target=data.get("target", ""),
            events=[NotificationEvent(e) for e in data.get("events", ["condition_met"])],
            headers=data.get("headers", {}),
        )
