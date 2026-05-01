"""
TAOS Tests — Notification Engine.

Tests Notification Config loading, payload building, and task integration.
"""

import pytest
import time
from taos.core.notifications.models import (
    NotificationChannel,
    NotificationEvent,
    NotificationConfig,
)
from taos.core.notifications.notifier import NotificationManager
from taos.core.tasks.task_model import TaskExecution, Task


class TestNotificationModels:
    def test_config_initialization(self):
        config = NotificationConfig(
            channel=NotificationChannel.WEBHOOK,
            target="https://api.example.com",
            events=[NotificationEvent.CONDITION_MET],
            headers={"Authorization": "Bearer tk"},
        )
        assert config.channel == NotificationChannel.WEBHOOK
        assert config.target == "https://api.example.com"

    def test_config_from_dict(self):
        data = {
            "channel": "email",
            "target": "user@example.com",
            "events": ["task_failed"],
        }
        config = NotificationConfig.from_dict(data)
        assert config.channel == NotificationChannel.EMAIL
        assert NotificationEvent.TASK_FAILED in config.events

    def test_config_to_dict(self):
        config = NotificationConfig(
            channel=NotificationChannel.WEBHOOK,
            target="https://api.example.com",
        )
        data = config.to_dict()
        assert data["channel"] == "webhook"
        assert data["target"] == "https://api.example.com"
        assert "condition_met" in data["events"]

    def test_whatsapp_channel_config(self):
        data = {
            "channel": "whatsapp",
            "target": "+919999999999",
            "events": ["task_success"],
        }
        config = NotificationConfig.from_dict(data)
        assert config.channel == NotificationChannel.WHATSAPP
        assert NotificationEvent.TASK_SUCCESS in config.events


class TestNotifierPayload:
    def test_webhook_payload_structure(self):
        # We test the schema of the webhook payload strictly matches PRD
        notifier = NotificationManager()
        
        task = Task(
            task_id="task_123",
            name="Test Task",
            goal="Test Goal",
        )
        
        exec_obj = TaskExecution(
            execution_id="exec_abc",
            timestamp=1700000000.0,
            success=True,
            result="Found price: 4000",
            condition_met=True,
        )
        
        payload = notifier._build_webhook_payload(task, exec_obj, NotificationEvent.CONDITION_MET)
        
        # Verify strict payload shape
        assert payload["event"] == "condition_met"
        assert payload["task_id"] == "task_123"
        assert payload["status"] == "success"
        assert payload["result"] == "Found price: 4000"
        assert payload["condition_met"] is True
