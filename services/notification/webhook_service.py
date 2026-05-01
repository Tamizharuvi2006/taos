"""Webhook notification service."""

from __future__ import annotations

from typing import Any, Dict

import httpx


class WebhookService:
    async def send(self, data: Dict[str, Any]) -> Dict[str, str]:
        url = data.get("url", "")
        payload = data.get("payload", {})
        if not url:
            return {"status": "skipped", "channel": "webhook"}
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        return {"status": "sent", "channel": "webhook"}

