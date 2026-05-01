"""Push token registration routes (FCM/WebPush clients)."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from taos.apps.api.auth_context import require_uid
from taos.config.settings import get_settings
from taos.infra.firebase import init_firebase_admin
from taos.infra.persistence.shared_store import get_shared_store

router = APIRouter(prefix="/push", tags=["push"])
_store = get_shared_store()


class RegisterPushTokenRequest(BaseModel):
    token: str = Field(..., min_length=20)
    platform: str = Field(default="web")
    device_name: str = Field(default="")


@router.post("/register")
async def register_push_token(request: RegisterPushTokenRequest, raw_request: Request) -> Dict[str, Any]:
    user_id = require_uid(raw_request)
    doc_id = f"token_{abs(hash(request.token))}"
    payload = {
        "id": doc_id,
        "token": request.token,
        "platform": request.platform,
        "device_name": request.device_name,
        "updated_at": time.time(),
    }
    await _store.set("push_tokens", doc_id, payload, user_id=user_id)
    return {"ok": True, "id": doc_id}


@router.get("/tokens")
async def list_tokens(raw_request: Request) -> List[Dict[str, Any]]:
    user_id = require_uid(raw_request)
    return await _store.list("push_tokens", user_id=user_id, limit=100)


@router.get("/health")
async def push_health(raw_request: Request) -> Dict[str, Any]:
    """
    Return backend-side push readiness for current user.

    Note: Browser permission/service-worker status is frontend-side and should be
    combined in UI with this payload.
    """
    user_id = require_uid(raw_request)
    settings = get_settings()
    tokens = await _store.list("push_tokens", user_id=user_id, limit=200)
    token_count = len(tokens or [])

    reasons: List[str] = []
    fcm_available = False
    firebase_ready = False
    try:
        init_firebase_admin()
        firebase_ready = True
        try:
            from firebase_admin import messaging  # noqa: F401

            fcm_available = True
        except Exception:
            reasons.append("firebase_admin_messaging_unavailable")
    except Exception:
        reasons.append("firebase_admin_not_initialized")

    if token_count == 0:
        reasons.append("no_registered_push_tokens")
    if not fcm_available:
        reasons.append("fcm_send_unavailable")
    if settings.storage_backend.lower() in {"firebase", "firestore"} and not _store.is_available:
        reasons.append("persistence_fallback_memory_mode")

    return {
        "ok": token_count > 0 and fcm_available,
        "user_id": user_id,
        "token_count": token_count,
        "firebase_ready": firebase_ready,
        "fcm_available": fcm_available,
        "storage_backend": settings.storage_backend,
        "store_available": bool(getattr(_store, "is_available", False)),
        "reasons": sorted(set(reasons)),
    }
