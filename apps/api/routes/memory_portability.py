from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from taos.apps.api.auth_context import require_uid
from taos.apps.api.errors import raise_api_error
from taos.core.memory.memory_portability import GLOBAL_MEMORY_PORTABILITY, ImportConfirmSelection

router = APIRouter(prefix="/memory", tags=["memory-portability"])


class ExportProfileRequest(BaseModel):
    preferred_name: str = ""


class ImportAnalyzeRequest(BaseModel):
    text: str


class ImportConfirmItem(BaseModel):
    item_id: str
    selected: bool = True
    content: str = ""
    type: str = ""
    allow_sensitive: bool = False


class ImportConfirmRequest(BaseModel):
    import_session_id: str
    items: List[ImportConfirmItem] = Field(default_factory=list)


class ImportCancelRequest(BaseModel):
    import_session_id: str


@router.post("/export-profile")
async def export_profile(payload: ExportProfileRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    return GLOBAL_MEMORY_PORTABILITY.export_profile(uid, preferred_name=payload.preferred_name)


@router.post("/import/analyze")
async def analyze_import(payload: ImportAnalyzeRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    session = GLOBAL_MEMORY_PORTABILITY.analyze_import(uid, payload.text)
    return session.to_dict()


@router.post("/import/confirm")
async def confirm_import(payload: ImportConfirmRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        selections = [ImportConfirmSelection(**item.model_dump()) for item in payload.items]
        return GLOBAL_MEMORY_PORTABILITY.confirm_import(uid, payload.import_session_id, selections)
    except (KeyError, ValueError) as exc:
        raise_api_error(404, "MEMORY_IMPORT_SESSION_NOT_FOUND", str(exc), request.headers.get("X-Request-ID", "unknown"))


@router.post("/import/cancel")
async def cancel_import(payload: ImportCancelRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        return GLOBAL_MEMORY_PORTABILITY.cancel_import(uid, payload.import_session_id)
    except KeyError as exc:
        raise_api_error(404, "MEMORY_IMPORT_SESSION_NOT_FOUND", str(exc), request.headers.get("X-Request-ID", "unknown"))


@router.get("/import/history")
async def import_history(request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    return {"history": GLOBAL_MEMORY_PORTABILITY.history(uid)}
