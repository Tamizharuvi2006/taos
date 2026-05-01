from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from taos.apps.api.auth_context import require_uid
from taos.core.memory.memory_privacy import GLOBAL_MEMORY_PRIVACY

router = APIRouter(prefix="/memory", tags=["memory-privacy"])


class PrivacySettingsRequest(BaseModel):
    memory_enabled: bool | None = None
    reference_chat_history: bool | None = None
    project_memory_enabled: bool | None = None
    shared_memory_enabled: bool | None = None
    auto_memory_suggestions: bool | None = None
    memory_trace_visibility: str | None = None


@router.get("/privacy/settings")
async def get_privacy_settings(request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    return {"settings": GLOBAL_MEMORY_PRIVACY.settings(uid).to_dict()}


@router.patch("/privacy/settings")
async def update_privacy_settings(payload: PrivacySettingsRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    return {"settings": GLOBAL_MEMORY_PRIVACY.update_settings(uid, **payload.model_dump()).to_dict()}


@router.get("/export-all")
async def export_all_memory(request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    return GLOBAL_MEMORY_PRIVACY.export_all(uid)


@router.delete("/delete-all")
async def delete_all_memory(request: Request, hard: bool = Query(False)) -> Dict[str, Any]:
    uid = require_uid(request)
    return GLOBAL_MEMORY_PRIVACY.delete_all(uid, hard=hard)


@router.delete("/delete-imported")
async def delete_imported_memory(request: Request, hard: bool = Query(False)) -> Dict[str, Any]:
    uid = require_uid(request)
    return GLOBAL_MEMORY_PRIVACY.delete_imported(uid, hard=hard)


@router.delete("/delete-project/{project_id}")
async def delete_project_memory(project_id: str, request: Request, hard: bool = Query(False)) -> Dict[str, Any]:
    uid = require_uid(request)
    return GLOBAL_MEMORY_PRIVACY.delete_project(uid, project_id, hard=hard)


@router.get("/audit-log")
async def memory_audit_log(request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    return {"events": GLOBAL_MEMORY_PRIVACY.audit_log(uid)}
