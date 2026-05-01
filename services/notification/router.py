"""Notification channel router."""

from __future__ import annotations

from typing import Any, Dict

from taos.services.notification.email_service import EmailService
from taos.services.notification.webhook_service import WebhookService
from taos.services.notification.whatsapp_service import WhatsAppService


class NotificationRouter:
    def __init__(self) -> None:
        self._email = EmailService()
        self._webhook = WebhookService()
        self._whatsapp = WhatsAppService()

    async def send_notification(self, channel: str, data: Dict[str, Any]) -> Dict[str, str]:
        channel = (channel or "").lower()
        if channel == "email":
            return await self._email.send(data)
        if channel == "webhook":
            return await self._webhook.send(data)
        if channel == "whatsapp":
            return await self._whatsapp.send(data)
        return {"status": "skipped", "channel": channel or "unknown"}

