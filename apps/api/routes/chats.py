"""Chat session persistence routes."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from taos.apps.api.auth_context import require_uid
from taos.core.chat import PersistentChatManager

router = APIRouter(prefix="/chats", tags=["chats"])
_chat_managers: Dict[str, PersistentChatManager] = {}


def get_chat_manager(user_id: str) -> PersistentChatManager:
    if user_id not in _chat_managers:
        _chat_managers[user_id] = PersistentChatManager(user_id=user_id)
    return _chat_managers[user_id]


class ChatMessageInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    role: str
    text: str
    status: str = "done"
    ts: float = 0.0


class UpsertChatRequest(BaseModel):
    title: str = Field(default="New chat")
    messages: List[ChatMessageInput] = Field(default_factory=list)
    doc_ids: List[str] = Field(default_factory=list)


@router.get("", summary="List chats")
async def list_chats(raw_request: Request, response: Response) -> List[Dict[str, Any]]:
    started = time.perf_counter()
    manager = get_chat_manager(require_uid(raw_request))
    rows = await manager.list_chats()
    response.headers["X-Chat-Source"] = manager.last_list_source()
    response.headers["X-Persist-Ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
    return rows


@router.put("/{chat_id}", summary="Create or update chat")
async def upsert_chat(chat_id: str, request: UpsertChatRequest, raw_request: Request, response: Response) -> Dict[str, Any]:
    started = time.perf_counter()
    manager = get_chat_manager(require_uid(raw_request))
    result = await manager.upsert_chat(
        chat_id=chat_id,
        payload={
            "title": request.title,
            "messages": [m.model_dump() for m in request.messages],
            "doc_ids": [str(doc_id).strip() for doc_id in request.doc_ids if str(doc_id).strip()],
        },
    )
    response.headers["X-Persist-Ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
    return result


@router.delete("/{chat_id}", summary="Delete chat")
async def delete_chat(chat_id: str, raw_request: Request, response: Response) -> Dict[str, Any]:
    started = time.perf_counter()
    manager = get_chat_manager(require_uid(raw_request))
    ok = await manager.delete_chat(chat_id)
    response.headers["X-Persist-Ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
    return {"deleted": bool(ok), "chat_id": chat_id}
