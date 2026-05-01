from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from taos.apps.api.auth_context import require_uid
from taos.apps.api.errors import raise_api_error
from taos.core.memory.memory_conflict_resolver import GLOBAL_MEMORY_CONFLICT_RESOLVER
from taos.core.memory.user_memory_model import UserMemory

router = APIRouter(prefix="/memory/conflicts", tags=["memory-conflicts"])


class ConflictCandidate(BaseModel):
    content: str
    type: str = "saved_memory"
    tags: List[str] = Field(default_factory=list)


class ConflictDetectRequest(BaseModel):
    candidates: List[ConflictCandidate] = Field(default_factory=list)


class ConflictResolveRequest(BaseModel):
    action: str
    merged_content: str = ""


@router.get("")
async def list_conflicts(request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    return {"conflicts": [conflict.to_dict() for conflict in GLOBAL_MEMORY_CONFLICT_RESOLVER.list(uid)]}


@router.post("/detect")
async def detect_conflicts(payload: ConflictDetectRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    candidates = [UserMemory(user_id=uid, content=item.content, type=item.type, tags=item.tags) for item in payload.candidates]
    conflicts = GLOBAL_MEMORY_CONFLICT_RESOLVER.detect(uid, candidates)
    return {"conflicts": [conflict.to_dict() for conflict in conflicts]}


@router.post("/{conflict_id}/resolve")
async def resolve_conflict(conflict_id: str, payload: ConflictResolveRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        return GLOBAL_MEMORY_CONFLICT_RESOLVER.resolve(uid, conflict_id, payload.action, merged_content=payload.merged_content)
    except (KeyError, ValueError) as exc:
        raise_api_error(404, "MEMORY_CONFLICT_NOT_FOUND", str(exc), request.headers.get("X-Request-ID", "unknown"))
