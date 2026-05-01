"""
TAOS API — Progress Endpoint.

GET /progress/{request_id} — Check execution progress (Upgrade #5: Speed Perception)
GET /performance/stats — Get latency and cache stats (Upgrade #2)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request

from taos.apps.api.auth_context import require_admin, require_uid
from taos.apps.api.errors import raise_api_error
from taos.core.observability import get_metrics
from taos.core.performance.progress import get_tracker, active_tracker_count, get_tracker_owner

router = APIRouter(tags=["progress"])


@router.get(
    "/progress/{request_id}",
    summary="Get execution progress",
    description="Check real-time progress of an agent query.",
)
async def get_progress(request_id: str, raw_request: Request) -> Dict[str, Any]:
    """
    Get progress for a running query.

    Returns current phase, progress %, label, and history.
    """
    tracker = get_tracker(request_id)
    if not tracker:
        raise_api_error(
            404,
            "PROGRESS_NOT_FOUND",
            f"No active execution found for request: {request_id}",
            raw_request.headers.get("X-Request-ID", "unknown"),
        )
    uid = require_uid(raw_request)
    owner = get_tracker_owner(request_id)
    if owner and owner != uid:
        raise_api_error(
            403,
            "AUTH_FORBIDDEN",
            "Forbidden for this request scope",
            raw_request.headers.get("X-Request-ID", "unknown"),
        )

    current = tracker.current
    return {
        "request_id": request_id,
        "phase": current.phase.value,
        "label": current.label,
        "progress": current.progress_pct,
        "elapsed_ms": round(tracker.elapsed_ms, 1),
        "is_complete": tracker.is_complete,
        "history": tracker.history,
    }


@router.get(
    "/progress",
    summary="List active executions",
)
async def list_active(raw_request: Request) -> Dict[str, int]:
    """Get count of active executions."""
    _ = require_uid(raw_request)
    return {"active_executions": active_tracker_count()}


@router.get(
    "/observability/dashboard",
    summary="Observability dashboard metrics",
)
async def observability_dashboard(raw_request: Request) -> Dict[str, Any]:
    """
    Backend analytics for:
    - agent usage and failure rates
    - latency by phase
    - debate frequency
    - overall failure rate
    """
    _ = require_admin(raw_request)
    return get_metrics().snapshot()
