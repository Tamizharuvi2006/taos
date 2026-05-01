"""
TAOS Notifications â€” Dispatch system.

Handles non-blocking, async dispatch of Webhooks and Emails.
Up to 3 retries with exponential backoff for webhooks.
Uses Zoho ZeptoMail for emails.

PRD Requirement: Non-blocking, Task visibility.
"""

from __future__ import annotations

import asyncio
import httpx
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from datetime import datetime, UTC

from taos.core.notifications.models import NotificationConfig, NotificationChannel, NotificationEvent
from taos.config.settings import get_settings
from taos.infra.logging.logger import TAOSLogger
from taos.infra.persistence.store import InMemoryStore, StorageBackend

if TYPE_CHECKING:
    from taos.core.tasks.task_model import Task, TaskExecution

_logger = TAOSLogger(name="taos.notifier")


class NotificationManager:
    """Manages dispatch of notifications to configured channels."""

    def __init__(self, store: Optional[StorageBackend] = None) -> None:
        self._settings = get_settings()
        self._store = store or InMemoryStore()
        self._daily_user_count: Dict[str, int] = {}
        self._daily_bucket = datetime.now(UTC).strftime("%Y-%m-%d")

    def dispatch(
        self,
        task: Task,
        execution: TaskExecution,
        event: NotificationEvent,
        user_id: str = "default",
    ) -> None:
        """
        Dispatch notifications matching the event type in the background.

        This is completely NON-BLOCKING. Fire and forget.
        """
        if not task.notifications:
            return

        for config in task.notifications:
            if event in config.events or NotificationEvent.ALWAYS in config.events:
                if not self._allow_for_user(user_id):
                    _logger.warning("notifier.rate_limited", user_id=user_id, task_id=task.task_id)
                    continue
                # Dispatch as background asyncio task
                asyncio.create_task(
                    self._process_single_notification(config, task, execution, event, user_id)
                )

    async def _process_single_notification(
        self,
        config: NotificationConfig,
        task: Task,
        execution: TaskExecution,
        event: NotificationEvent,
        user_id: str,
    ) -> None:
        """Process a single notification with retry logic."""
        try:
            if config.channel == NotificationChannel.WEBHOOK:
                delivered = await self._send_webhook_with_retry(config, task, execution, event)
                await self._record_notification(
                    user_id,
                    config,
                    event,
                    "sent" if delivered else "skipped",
                    task,
                    execution,
                    error="" if delivered else "webhook_delivery_failed",
                )
            elif config.channel == NotificationChannel.EMAIL:
                delivered = await self._send_email(config, task, execution, event, user_id=user_id)
                await self._record_notification(
                    user_id,
                    config,
                    event,
                    "sent" if delivered else "skipped",
                    task,
                    execution,
                    error="" if delivered else "email_delivery_failed",
                )
            elif config.channel == NotificationChannel.WHATSAPP:
                await self._send_whatsapp(config, task, execution, event)
                await self._record_notification(user_id, config, event, "sent", task, execution)
            elif config.channel == NotificationChannel.TELEGRAM:
                await self._send_telegram(config, task, execution, event)
                await self._record_notification(user_id, config, event, "sent", task, execution)
            elif config.channel == NotificationChannel.FCM:
                delivered = await self._send_fcm(user_id, task, execution, event)
                await self._record_notification(
                    user_id,
                    config,
                    event,
                    "sent" if delivered else "skipped",
                    task,
                    execution,
                    error="" if delivered else "fcm_delivery_failed_or_no_tokens",
                )
            else:
                _logger.warning("notifier.unsupported_channel", channel=config.channel.value)
        except Exception as e:
            await self._record_notification(user_id, config, event, "failed", task, execution, error=str(e))
            _logger.error(
                "notifier.dispatch_failed",
                channel=config.channel.value,
                target=config.target,
                error=str(e),
                task_id=task.task_id,
            )

    def _allow_for_user(self, user_id: str) -> bool:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self._daily_bucket:
            self._daily_bucket = today
            self._daily_user_count = {}
        current = self._daily_user_count.get(user_id, 0)
        if current >= self._settings.max_notifications_per_day:
            return False
        self._daily_user_count[user_id] = current + 1
        return True

    async def _record_notification(
        self,
        user_id: str,
        config: NotificationConfig,
        event: NotificationEvent,
        status: str,
        task: Task,
        execution: TaskExecution,
        error: str = "",
    ) -> None:
        payload = {
            "id": "",
            "channel": config.channel.value,
            "target": config.target,
            "event": event.value,
            "status": status,
            "task_id": task.task_id,
            "execution_id": execution.execution_id,
            "message": self._compact_notification_text(execution, error=error),
            "sent_at": time.time(),
            "error": error,
        }
        doc_id = f"notif_{task.task_id}_{execution.execution_id}_{int(payload['sent_at'])}_{config.channel.value}"
        payload["id"] = doc_id
        await self._store.set("notifications", doc_id, payload, user_id=user_id)

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # WEBHOOK CONTROLLER
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    async def _send_webhook_with_retry(
        self,
        config: NotificationConfig,
        task: Task,
        execution: TaskExecution,
        event: NotificationEvent,
        max_retries: int = 3,
    ) -> bool:
        """Send webhook with exponential backoff retry."""
        payload = self._build_webhook_payload(task, execution, event)
        headers = {**config.headers, "Content-Type": "application/json"}

        # Attempt sending
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(config.target, json=payload, headers=headers)
                    response.raise_for_status()
                _logger.info("notifier.webhook_success", target=config.target, task_id=task.task_id)
                return True
            except (httpx.HTTPError, httpx.TimeoutException) as getattr_err:
                _logger.warning(
                    "notifier.webhook_failed",
                    target=config.target,
                    attempt=attempt + 1,
                    error=str(getattr_err),
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s

        # Failed
        _logger.error("notifier.webhook_aborted", target=config.target, task_id=task.task_id)
        return False

    def _build_webhook_payload(self, task: Task, execution: TaskExecution, event: NotificationEvent) -> Dict[str, Any]:
        """Build a strict, structured payload for webhooks."""
        return {
            "event": event.value,
            "task_id": task.task_id,
            "task_name": task.name,
            "status": "success" if execution.success else "failed",
            "condition_met": execution.condition_met,
            "timestamp": datetime.fromtimestamp(execution.timestamp, UTC).isoformat().replace("+00:00", "Z"),
            "result": execution.result,
            "error": execution.error,
        }

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # EMAIL CONTROLLER
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    async def _send_email(
        self,
        config: NotificationConfig,
        task: Task,
        execution: TaskExecution,
        event: NotificationEvent,
        user_id: str = "default",
    ) -> bool:
        """Send email via Zoho ZeptoMail API (or mock standard out if not configured)."""
        raw_key = (self._settings.zeptomail_api_key or "").strip().strip('"').strip("'")
        api_key = raw_key
        from_email = self._settings.zeptomail_from_email or "taos@example.com"

        subject = getattr(execution, "email_subject", None) or f"Relyce AI Â· Task Update: {task.name}"
        subject = subject.replace("Ã‚Â·", "-").replace("Â·", "-")
        result_text = self._compact_notification_text(execution, error=execution.error or "").replace("\n", "<br/>")
        profile_name = await self._resolve_user_display_name(user_id=user_id)
        custom_html = getattr(execution, "email_html", None)
        html_content = custom_html or self._build_relyce_email_template(
            title="Task Update",
            subtitle=f"{'Success' if execution.success else 'Failed'} Â· {event.value.upper()}",
            body_html=(
                f"<p style='margin:0 0 12px 0;'>Hello {profile_name},</p>"
                f"<p style='margin:0 0 12px 0;'><strong>Task:</strong> {task.name}</p>"
                f"<p style='margin:0 0 12px 0;'><strong>Update:</strong> {result_text}</p>"
                "<p style='margin:0;'>Open your Relyce AI workspace to view full details.</p>"
            ),
            cta_text="Open Relyce Workspace",
            cta_url=(self._settings.site_url or "").strip() or "https://relyce.com",
            footer_note="This is an automated notification from Relyce AI.",
        )

        if not api_key:
            _logger.info(
                "notifier.mock_email",
                target=config.target,
                subject=subject,
                msg="ZEPTOMAIL_API_KEY not configured. Simulating email send.",
            )
            return True

        # ZeptoMail API can be region-specific; try configured endpoint first.
        preferred_url = (self._settings.zeptomail_api_url or "").strip()
        candidate_urls = [u for u in [preferred_url, "https://api.zeptomail.in/v1.1/email", "https://api.zeptomail.com/v1.1/email"] if u]
        # Keep order and remove duplicates.
        seen = set()
        urls: List[str] = []
        for u in candidate_urls:
            if u not in seen:
                seen.add(u)
                urls.append(u)

        # Normalize auth token format.
        if api_key.lower().startswith("zoho-enczapikey"):
            token = api_key.split(" ", 1)[-1].strip()
        else:
            token = api_key.strip()
        auth_header = f"Zoho-enczapikey {token}"
        
        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        payload = {
            "from": {"address": from_email},
            "to": [{"email_address": {"address": config.target}}],
            "subject": subject,
            "htmlbody": html_content,
        }

        max_retries = 3
        for attempt in range(max_retries):
            for url in urls:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.post(url, headers=headers, json=payload)
                        resp.raise_for_status()
                        _logger.info("notifier.email_success", target=config.target, task_id=task.task_id, url=url)
                        return True
                except httpx.HTTPError as he:
                    _logger.error(
                        "notifier.email_failed",
                        target=config.target,
                        attempt=attempt + 1,
                        url=url,
                        error=str(he),
                    )
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

        _logger.error("notifier.email_aborted", target=config.target, task_id=task.task_id)
        return False

    def _build_relyce_email_template(
        self,
        title: str,
        subtitle: str,
        body_html: str,
        cta_text: str = "",
        cta_url: str = "",
        footer_note: str = "",
    ) -> str:
        logo_url = (self._settings.zeptomail_logo_url or "").strip()
        if not logo_url:
            site_url = (self._settings.site_url or "").strip().rstrip("/")
            if site_url:
                logo_url = f"{site_url}/logo.svg"
        subtitle = subtitle.replace("Â·", "-").replace("·", "-")
        logo_html = (
            f'<img src="{logo_url}" alt="Relyce AI" width="120" style="display:block;margin:0 auto 16px auto;" />'
            if logo_url
            else '<div style="font-size:18px;font-weight:700;letter-spacing:0.04em;color:#10b981;margin-bottom:12px;">RELYCE AI</div>'
        )
        cta_html = ""
        if cta_text and cta_url:
            cta_html = (
                f'<a href="{cta_url}" '
                'style="display:inline-block;margin-top:16px;padding:10px 16px;'
                'background:#10b981;color:#04120d;text-decoration:none;border-radius:8px;'
                'font-weight:700;">'
                f"{cta_text}</a>"
            )
        footer = (footer_note or "Relyce AI - Automation & Workflow Intelligence").replace("Â·", "-").replace("·", "-")

        return f"""
        <div style="margin:0;padding:0;background:#05070b;font-family:Segoe UI,Arial,sans-serif;color:#e5e7eb;">
          <div style="max-width:640px;margin:24px auto;border:1px solid #1f2937;border-radius:14px;overflow:hidden;background:#0b0f17;">
            <div style="padding:22px 24px;border-bottom:1px solid #1f2937;text-align:center;background:linear-gradient(180deg,#0f172a,#0b0f17);">
              {logo_html}
              <div style="font-size:22px;font-weight:700;color:#ffffff;margin-bottom:6px;">{title}</div>
              <div style="font-size:13px;color:#9ca3af;">{subtitle}</div>
            </div>
            <div style="padding:22px 24px;font-size:14px;line-height:1.7;color:#d1d5db;">
              {body_html}
              {cta_html}
            </div>
            <div style="padding:14px 24px;border-top:1px solid #1f2937;font-size:12px;color:#6b7280;">
              {footer}
            </div>
          </div>
        </div>
        """

    async def _resolve_user_display_name(self, user_id: str) -> str:
        try:
            profile = await self._store.get("profiles", "me", user_id=user_id) or {}
            name = str(profile.get("display_name") or profile.get("name") or "").strip()
            if name:
                return name
        except Exception:
            pass
        return "there"

    def _compact_notification_text(self, execution: "TaskExecution", error: str = "") -> str:
        source = str(execution.result or "").strip()
        if not source:
            source = (error or execution.error or "Task update").strip()
        source = source.replace("\r", " ").replace("\n", " ")
        source = " ".join(source.split())
        if len(source) > 160:
            source = source[:157].rstrip() + "..."
        return source

    async def _send_whatsapp(
        self,
        config: NotificationConfig,
        task: Task,
        execution: TaskExecution,
        event: NotificationEvent,
    ) -> None:
        """Send WhatsApp notification via configured provider webhook."""
        endpoint = config.target if config.target.startswith("http") else self._settings.whatsapp_webhook_url
        if not endpoint:
            _logger.warning(
                "notifier.whatsapp_skipped",
                reason="missing_endpoint",
                task_id=task.task_id,
            )
            return

        message = (
            f"TAOS Alert\n"
            f"Task: {task.name}\n"
            f"Event: {event.value}\n"
            f"Status: {'success' if execution.success else 'failed'}\n"
            f"Result: {str(execution.result)[:300] if execution.result else execution.error or 'no result'}"
        )
        payload = {
            "to": config.target if not config.target.startswith("http") else "",
            "message": message,
            "task_id": task.task_id,
            "event": event.value,
            "success": execution.success,
        }
        headers = {"Content-Type": "application/json"}
        if self._settings.whatsapp_api_key:
            headers["Authorization"] = f"Bearer {self._settings.whatsapp_api_key}"

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(endpoint, headers=headers, json=payload)
                    resp.raise_for_status()
                    _logger.info("notifier.whatsapp_success", target=config.target, task_id=task.task_id)
                    return
            except httpx.HTTPError as err:
                _logger.warning(
                    "notifier.whatsapp_failed",
                    target=config.target,
                    attempt=attempt + 1,
                    error=str(err),
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        _logger.error("notifier.whatsapp_aborted", target=config.target, task_id=task.task_id)

    async def _send_telegram(
        self,
        config: NotificationConfig,
        task: Task,
        execution: TaskExecution,
        event: NotificationEvent,
    ) -> None:
        """Send Telegram notification using bot API."""
        bot_token = self._settings.telegram_bot_token
        chat_id = config.target or self._settings.telegram_chat_id
        if not bot_token or not chat_id:
            _logger.warning(
                "notifier.telegram_skipped",
                reason="missing_bot_or_chat_id",
                task_id=task.task_id,
            )
            return

        text = (
            "TAOS Reminder\n"
            f"Task: {task.name}\n"
            f"Event: {event.value}\n"
            f"Status: {'success' if execution.success else 'failed'}\n"
            f"Message: {str(execution.result)[:500] if execution.result else execution.error or 'no result'}"
        )
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    _logger.info("notifier.telegram_success", chat_id=chat_id, task_id=task.task_id)
                    return
            except httpx.HTTPError as err:
                _logger.warning(
                    "notifier.telegram_failed",
                    chat_id=chat_id,
                    attempt=attempt + 1,
                    error=str(err),
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        _logger.error("notifier.telegram_aborted", chat_id=chat_id, task_id=task.task_id)

    async def _send_fcm(
        self,
        user_id: str,
        task: Task,
        execution: TaskExecution,
        event: NotificationEvent,
    ) -> bool:
        """Send push notification via Firebase Cloud Messaging."""
        try:
            import firebase_admin
            from firebase_admin import messaging
            from taos.infra.firebase import init_firebase_admin
        except Exception:
            _logger.warning("notifier.fcm_unavailable", reason="firebase_admin_missing")
            return False

        try:
            init_firebase_admin()
        except Exception as e:
            _logger.warning("notifier.fcm_init_failed", error=str(e))
            return False

        tokens = await self._store.list("push_tokens", user_id=user_id, limit=200)
        reg_tokens = [str(t.get("token", "")).strip() for t in tokens if str(t.get("token", "")).strip()]
        if not reg_tokens:
            _logger.info("notifier.fcm_no_tokens", user_id=user_id, task_id=task.task_id)
            return False

        body = str(execution.result)[:240] if execution.result else (execution.error or "Task update")
        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=f"TAOS Â· {task.name}",
                body=body,
            ),
            data={
                "task_id": task.task_id,
                "execution_id": execution.execution_id,
                "event": event.value,
                "status": "success" if execution.success else "failed",
            },
            tokens=reg_tokens,
        )

        try:
            resp = messaging.send_each_for_multicast(message)
            _logger.info(
                "notifier.fcm_sent",
                task_id=task.task_id,
                success_count=resp.success_count,
                failure_count=resp.failure_count,
            )
            return bool(resp.success_count)
        except Exception as e:
            _logger.warning("notifier.fcm_send_failed", error=str(e), task_id=task.task_id)
            return False

