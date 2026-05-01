"""WhatsApp notification service."""

from __future__ import annotations

from typing import Any, Dict

import httpx

from taos.config.settings import get_settings


class WhatsAppService:
    def __init__(self) -> None:
        self._settings = get_settings()

    async def send(self, data: Dict[str, Any]) -> Dict[str, str]:
        endpoint = data.get("url") or self._settings.whatsapp_webhook_url
        if not endpoint:
            return {"status": "skipped", "channel": "whatsapp"}

        headers = {"Content-Type": "application/json"}
        if self._settings.whatsapp_api_key:
            headers["Authorization"] = f"Bearer {self._settings.whatsapp_api_key}"
        payload = {
            "to": data.get("to", ""),
            "message": data.get("message", ""),
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
        return {"status": "sent", "channel": "whatsapp"}

