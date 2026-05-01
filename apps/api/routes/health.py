"""
TAOS API — Health check endpoint.
"""

from __future__ import annotations

import time

from fastapi import APIRouter

from taos.apps.api.schemas.response import HealthResponse
from taos.core.deployment.readiness import build_readiness_report
from taos.config.settings import get_settings

router = APIRouter(tags=["health"])

_start_time = time.time()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint. Returns service status and uptime."""
    settings = get_settings()
    readiness = build_readiness_report(settings=settings)
    return HealthResponse(
        status="healthy" if readiness.get("ready") else "degraded",
        version="0.1.0",
        uptime_seconds=round(time.time() - _start_time, 2),
        environment=settings.taos_env,
        ready=bool(readiness.get("ready")),
        checks=dict(readiness.get("checks") or {}),
        warnings=list(readiness.get("warnings") or []),
    )


@router.get("/warmup")
async def warmup() -> dict:
    """Lightweight warmup endpoint for scheduled cold-start mitigation pings."""
    return {"ok": True, "warmed": True, "timestamp": time.time()}
