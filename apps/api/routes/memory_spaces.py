from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from taos.apps.api.auth_context import require_uid
from taos.apps.api.errors import raise_api_error
from taos.core.memory.memory_space_store import GLOBAL_MEMORY_SPACE_STORE

router = APIRouter(prefix="/memory/spaces", tags=["memory-spaces"])


class SpaceCreateRequest(BaseModel):
    name: str
    scope: str = "project"


class SpaceMemoryRequest(BaseModel):
    content: str
    tags: List[str] = Field(default_factory=list)


class SpaceMemoryUpdateRequest(BaseModel):
    content: str | None = None
    status: str | None = None
    tags: List[str] | None = None


class SpaceMemberRequest(BaseModel):
    user_id: str
    role: str = "viewer"


@router.get("")
async def list_spaces(request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    return {"spaces": [space.to_dict() for space in GLOBAL_MEMORY_SPACE_STORE.list_spaces(uid)]}


@router.post("")
async def create_space(payload: SpaceCreateRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        return {"space": GLOBAL_MEMORY_SPACE_STORE.create_space(payload.name, uid, scope=payload.scope).to_dict()}
    except ValueError as exc:
        raise_api_error(400, "MEMORY_SPACE_VALIDATION_ERROR", str(exc), request.headers.get("X-Request-ID", "unknown"))


@router.get("/{space_id}")
async def get_space(space_id: str, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        return {"space": GLOBAL_MEMORY_SPACE_STORE.get_space(space_id, uid).to_dict()}
    except (KeyError, PermissionError) as exc:
        raise_api_error(403, "MEMORY_SPACE_FORBIDDEN", str(exc), request.headers.get("X-Request-ID", "unknown"))


@router.post("/{space_id}/memories")
async def add_space_memory(space_id: str, payload: SpaceMemoryRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        memory = GLOBAL_MEMORY_SPACE_STORE.add_memory(space_id, uid, payload.content, tags=payload.tags)
        return {"memory": memory.to_dict()}
    except (KeyError, PermissionError) as exc:
        raise_api_error(403, "MEMORY_SPACE_FORBIDDEN", str(exc), request.headers.get("X-Request-ID", "unknown"))


@router.patch("/{space_id}/memories/{memory_id}")
async def update_space_memory(space_id: str, memory_id: str, payload: SpaceMemoryUpdateRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        memory = GLOBAL_MEMORY_SPACE_STORE.update_memory(space_id, uid, memory_id, **payload.model_dump())
        return {"memory": memory.to_dict()}
    except (KeyError, PermissionError, ValueError) as exc:
        raise_api_error(403, "MEMORY_SPACE_FORBIDDEN", str(exc), request.headers.get("X-Request-ID", "unknown"))


@router.delete("/{space_id}/memories/{memory_id}")
async def delete_space_memory(space_id: str, memory_id: str, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        return {"deleted": GLOBAL_MEMORY_SPACE_STORE.delete_memory(space_id, uid, memory_id)}
    except (KeyError, PermissionError) as exc:
        raise_api_error(403, "MEMORY_SPACE_FORBIDDEN", str(exc), request.headers.get("X-Request-ID", "unknown"))


@router.post("/{space_id}/members")
async def add_space_member(space_id: str, payload: SpaceMemberRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        return {"space": GLOBAL_MEMORY_SPACE_STORE.add_member(space_id, uid, payload.user_id, payload.role).to_dict()}
    except (KeyError, PermissionError, ValueError) as exc:
        raise_api_error(403, "MEMORY_SPACE_FORBIDDEN", str(exc), request.headers.get("X-Request-ID", "unknown"))


@router.delete("/{space_id}/members/{user_id}")
async def remove_space_member(space_id: str, user_id: str, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        return {"space": GLOBAL_MEMORY_SPACE_STORE.remove_member(space_id, uid, user_id).to_dict()}
    except (KeyError, PermissionError) as exc:
        raise_api_error(403, "MEMORY_SPACE_FORBIDDEN", str(exc), request.headers.get("X-Request-ID", "unknown"))
