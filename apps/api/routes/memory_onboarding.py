from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from taos.apps.api.auth_context import require_uid
from taos.apps.api.errors import raise_api_error
from taos.core.memory.memory_portability import ImportConfirmSelection
from taos.core.memory.onboarding_memory_builder import OnboardingMemoryBuilder
from taos.core.memory.onboarding_model import OnboardingAnswers
from taos.core.memory.user_memory_store import GLOBAL_USER_MEMORY_STORE

router = APIRouter(prefix="/memory/onboarding", tags=["memory-onboarding"])
GLOBAL_ONBOARDING_BUILDER = OnboardingMemoryBuilder(GLOBAL_USER_MEMORY_STORE)


class OnboardingAnalyzeRequest(BaseModel):
    preferred_name: str = ""
    assistant_style: str = ""
    language_tone: str = ""
    goals: str = ""
    technical_stack: str = ""
    active_projects: str = ""
    current_blockers: str = ""
    output_preferences: str = ""
    boundaries: str = ""


class OnboardingConfirmItem(BaseModel):
    item_id: str
    selected: bool = True
    content: str = ""
    type: str = ""
    allow_sensitive: bool = False


class OnboardingConfirmRequest(BaseModel):
    items: List[OnboardingConfirmItem] = Field(default_factory=list)


@router.get("/status")
async def onboarding_status(request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    return GLOBAL_ONBOARDING_BUILDER.status(uid).to_dict()


@router.post("/analyze")
async def onboarding_analyze(payload: OnboardingAnalyzeRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    session = GLOBAL_ONBOARDING_BUILDER.analyze(uid, OnboardingAnswers(**payload.model_dump()))
    return session.to_dict()


@router.post("/confirm")
async def onboarding_confirm(payload: OnboardingConfirmRequest, request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    try:
        selections = [ImportConfirmSelection(**item.model_dump()) for item in payload.items]
        return GLOBAL_ONBOARDING_BUILDER.confirm(uid, selections)
    except KeyError as exc:
        raise_api_error(404, "ONBOARDING_SESSION_NOT_FOUND", str(exc), request.headers.get("X-Request-ID", "unknown"))


@router.post("/skip")
async def onboarding_skip(request: Request) -> Dict[str, Any]:
    uid = require_uid(request)
    return GLOBAL_ONBOARDING_BUILDER.skip(uid).to_dict()
