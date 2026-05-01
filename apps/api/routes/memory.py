from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel, Field

from taos.apps.api.auth_context import require_uid
from taos.apps.api.errors import raise_api_error
from taos.core.memory.memory_audit import build_memory_audit_report
from taos.core.memory.memory_extractor import extract_memories_from_text, summarize_visible_memories
from taos.core.memory.user_memory_model import UserMemory
from taos.core.memory.user_memory_store import GLOBAL_USER_MEMORY_STORE

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryCreateRequest(BaseModel):
    type: str = "saved_memory"
    content: str
    source_chat_id: str = ""
    source_message_id: str = ""
    confidence: float = 0.85
    user_visible: bool = True
    tags: List[str] = Field(default_factory=list)
    reason_saved: str = "User created this memory manually."


class MemoryUpdateRequest(BaseModel):
    type: str | None = None
    content: str | None = None
    confidence: float | None = None
    status: str | None = None
    user_visible: bool | None = None
    tags: List[str] | None = None
    reason_saved: str | None = None


class MemoryExtractRequest(BaseModel):
    text: str
    source_chat_id: str = ""
    source_message_id: str = ""


class MemorySettingsRequest(BaseModel):
    memory_enabled: bool | None = None
    reference_chat_history_enabled: bool | None = None


@router.get("")
async def list_memories(request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    memories = GLOBAL_USER_MEMORY_STORE.list(uid)
    return {
        "settings": GLOBAL_USER_MEMORY_STORE.settings_for(uid).to_dict(),
        "memories": [memory.to_dict() for memory in memories],
        "summary": summarize_visible_memories(memories),
    }


@router.post("")
async def create_memory(payload: MemoryCreateRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        memory = UserMemory(user_id=uid, **payload.model_dump()).validate()
        GLOBAL_USER_MEMORY_STORE.create(memory)
        return {"memory": memory.to_dict()}
    except ValueError as exc:
        raise_api_error(400, "MEMORY_VALIDATION_ERROR", str(exc), request.headers.get("X-Request-ID", "unknown"))


@router.patch("/settings")
async def update_memory_settings(payload: MemorySettingsRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    settings = GLOBAL_USER_MEMORY_STORE.update_settings(uid, **payload.model_dump())
    return {"settings": settings.to_dict()}


@router.patch("/{memory_id}")
async def update_memory(memory_id: str, payload: MemoryUpdateRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        memory = GLOBAL_USER_MEMORY_STORE.update(uid, memory_id, **payload.model_dump())
        return {"memory": memory.to_dict()}
    except (KeyError, ValueError) as exc:
        raise_api_error(404, "MEMORY_NOT_FOUND", str(exc), request.headers.get("X-Request-ID", "unknown"))


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    deleted = GLOBAL_USER_MEMORY_STORE.delete(uid, memory_id)
    return {"deleted": deleted, "memory_id": memory_id}


@router.delete("")
async def delete_all_memories(request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    count = GLOBAL_USER_MEMORY_STORE.delete_all(uid)
    return {"deleted": count}


@router.post("/extract")
async def extract_memory(payload: MemoryExtractRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    extraction = extract_memories_from_text(
        payload.text,
        user_id=uid,
        source_chat_id=payload.source_chat_id,
        source_message_id=payload.source_message_id,
    )
    saved = []
    for memory in extraction.memories:
        GLOBAL_USER_MEMORY_STORE.create(memory)
        saved.append(memory.to_dict())
    return {
        "action": extraction.action,
        "memories": saved,
        "blocked_reason": extraction.blocked_reason,
        "forget_query": extraction.forget_query,
    }


@router.get("/audit")
async def memory_audit(request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    return build_memory_audit_report(GLOBAL_USER_MEMORY_STORE, uid)
