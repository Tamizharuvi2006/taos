"""Email notification service."""

from __future__ import annotations

from typing import Dict

from taos.infra.logging.logger import TAOSLogger


class EmailService:
    def __init__(self) -> None:
        self._logger = TAOSLogger(name="taos.notify.email")

    async def send(self, data: Dict[str, str]) -> Dict[str, str]:
        # Provider integration is handled in core notifier for now.
        self._logger.info("notify.email.queued", target=data.get("to", ""))
        return {"status": "queued", "channel": "email"}

