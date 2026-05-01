"""In-app notification feed routes."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from taos.apps.api.auth_context import require_uid
from taos.apps.api.routes.tasks import get_task_manager

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationResponse(BaseModel):
    id: str
    channel: str
    event: str
    status: str
    task_id: str
    task_name: str = ""
    execution_id: str
    message: str = ""
    sent_at: float
    next_run_at: float = 0.0


@router.get("", response_model=List[NotificationResponse], summary="List recent notifications")
async def list_notifications(
    raw_request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    status: Optional[str] = Query(default=None),
) -> List[NotificationResponse]:
    uid = require_uid(raw_request)
    manager = get_task_manager(uid)

    sorted_docs = await manager.list_notifications(limit=limit, status=status)
    return [
        NotificationResponse(
            id=str(d.get("id") or d.get("execution_id") or ""),
            channel=str(d.get("channel") or ""),
            event=str(d.get("event") or ""),
            status=str(d.get("status") or ""),
            task_id=str(d.get("task_id") or ""),
            task_name=str(d.get("task_name") or ""),
            execution_id=str(d.get("execution_id") or ""),
            message=str(d.get("message") or ""),
            sent_at=float(d.get("sent_at") or 0.0),
            next_run_at=float(d.get("next_run_at") or 0.0),
        )
        for d in sorted_docs
    ]
