"""
TAOS API — Task Management Routes.

POST   /tasks         — Create a new task
GET    /tasks         — List all tasks
GET    /tasks/{id}    — Get task details
POST   /tasks/{id}/run — Execute a task now
POST   /tasks/{id}/pause — Pause a task
POST   /tasks/{id}/resume — Resume a task
DELETE /tasks/{id}    — Delete a task
GET    /tasks/{id}/history — Get execution history
GET    /tasks/stats   — Get system stats
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from taos.apps.api.auth_context import current_user_info, require_uid
from taos.core.tasks.task_model import (
    TaskType,
    TaskStatus,
    TriggerType,
    ScheduleConfig,
    ConditionConfig,
)
from taos.core.tasks.persistent_manager import PersistentTaskManager
from taos.core.tasks.scheduler_service import PersistentTaskScheduler
from taos.core.tasks.chat_task_intent import detect_chat_task_intent
from taos.core.notifications.models import NotificationConfig, NotificationChannel, NotificationEvent
from taos.config.settings import get_settings
from taos.infra.firebase import get_firestore_client
from taos.infra.persistence.shared_store import get_shared_store

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Task managers scoped by authenticated user
_task_managers: Dict[str, PersistentTaskManager] = {}
_task_scheduler: Optional[PersistentTaskScheduler] = None
_store = get_shared_store()


def get_task_manager(user_id: str) -> PersistentTaskManager:
    if user_id not in _task_managers:
        _task_managers[user_id] = PersistentTaskManager(user_id=user_id)
    return _task_managers[user_id]


def get_task_managers() -> Dict[str, PersistentTaskManager]:
    return _task_managers


async def start_scheduler() -> None:
    global _task_scheduler
    if _task_scheduler is not None:
        return
    settings = get_settings()
    _task_scheduler = PersistentTaskScheduler(
        get_managers=get_task_managers,
        poll_seconds=max(5, int(settings.task_scheduler_poll_seconds)),
        max_concurrent_runs=max(1, int(settings.task_scheduler_max_concurrent)),
    )
    await _task_scheduler.start()


async def stop_scheduler() -> None:
    global _task_scheduler
    if _task_scheduler is None:
        return
    await _task_scheduler.stop()
    _task_scheduler = None


def scheduler_stats() -> Dict[str, Any]:
    if _task_scheduler is None:
        return {"running": False}
    return _task_scheduler.stats


# ═══════════════════════════════════════════════════════════
# REQUEST/RESPONSE MODELS
# ═══════════════════════════════════════════════════════════

class ScheduleInput(BaseModel):
    interval_seconds: int = Field(0, description="Run every N seconds")
    max_runs: int = Field(0, description="0 = unlimited")
    run_immediately: bool = Field(True)


class ConditionInput(BaseModel):
    field: str = Field("", description="Field to check in result")
    operator: str = Field("", description="lt, gt, eq, ne, contains, changed")
    threshold: Any = Field(None, description="Value to compare against")
    action_on_true: str = Field("notify", description="Action if condition met")
    action_on_false: str = Field("log", description="Action if not met")


class NotificationInput(BaseModel):
    channel: str = Field("webhook", description="webhook | email")
    target: str = Field(..., description="URL or Email address")
    events: List[str] = Field(default_factory=lambda: ["condition_met"])
    headers: Dict[str, str] = Field(default_factory=dict)


class CreateTaskRequest(BaseModel):
    goal: str = Field(..., min_length=3, description="What to accomplish")
    name: str = Field("", description="Human-readable name")
    description: str = Field("", description="Optional description")
    task_type: str = Field("simple", description="simple | workflow")
    trigger: str = Field("manual", description="manual | time_based | event_based")
    schedule: Optional[ScheduleInput] = None
    workflow_id: str = Field("", description="Optional workflow id to execute")
    workflow_context: Dict[str, Any] = Field(default_factory=dict, description="Optional workflow input context")
    condition: Optional[ConditionInput] = None
    notifications: List[NotificationInput] = Field(default_factory=list)
    max_retries: int = Field(3, ge=0, le=10, description="Maximum retries after first failure")
    min_confidence: float = Field(0.0, ge=0.0, le=1.0, description="Optional confidence gate for task success")
    retry_policy: Dict[str, int] = Field(default_factory=dict, description="Failure-type retry overrides")
    tags: List[str] = Field(default_factory=list)

    model_config = {"json_schema_extra": {
        "examples": [{
            "goal": "Check if Python 3.13 is released",
            "name": "Python Release Checker",
            "trigger": "time_based",
            "schedule": {"interval_seconds": 86400, "run_immediately": True},
            "tags": ["monitoring"],
        }]
    }}


class TaskResponse(BaseModel):
    task_id: str
    name: str
    goal: str
    status: str
    task_type: str
    trigger_type: str
    workflow_id: str = ""
    is_recurring: bool
    run_count: int
    success_count: int
    last_result: Optional[Dict[str, Any]] = None
    last_run_at: float = 0.0
    created_at: float
    next_run_at: float
    total_cost: float
    max_retries: int = 3
    min_confidence: float = 0.0
    retry_policy: Dict[str, int] = Field(default_factory=dict)
    tags: List[str] = []


class ExecutionResponse(BaseModel):
    execution_id: str
    success: bool
    result: Optional[str] = None
    error: Optional[str] = None
    confidence: float = 0.0
    elapsed_ms: float = 0.0
    attempts: int = 1
    retry_count: int = 0
    failure_type: str = ""
    confidence_gate: float = 0.0
    condition_met: Optional[bool] = None
    action_taken: str = ""


class ChatToTaskRequest(BaseModel):
    query: str = Field(..., min_length=3)
    auto_create: bool = Field(default=False)
    name: str = Field(default="")
    tags: List[str] = Field(default_factory=list)


class ChatToTaskResponse(BaseModel):
    task_intent: bool
    suggested_name: str
    normalized_goal: str
    trigger_type: str
    interval_seconds: int = 0
    task_id: Optional[str] = None


# ═══════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════

@router.post("", response_model=TaskResponse, summary="Create a new task")
async def create_task(request: CreateTaskRequest, raw_request: Request) -> TaskResponse:
    """Create an automated task."""
    manager = get_task_manager(require_uid(raw_request))

    # Build schedule config
    schedule = None
    if request.schedule and request.trigger == "time_based":
        schedule = ScheduleConfig(
            interval_seconds=request.schedule.interval_seconds,
            max_runs=request.schedule.max_runs,
            run_immediately=request.schedule.run_immediately,
        )

    # Build condition config
    condition = None
    if request.condition and request.condition.field:
        condition = ConditionConfig(
            field=request.condition.field,
            operator=request.condition.operator,
            threshold=request.condition.threshold,
            action_on_true=request.condition.action_on_true,
            action_on_false=request.condition.action_on_false,
        )

    try:
        trigger = TriggerType(request.trigger)
        task_type = TaskType(request.task_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Build notifications
    notifications = []
    if request.notifications:
        for n in request.notifications:
            notifications.append(
                NotificationConfig(
                    channel=NotificationChannel(n.channel),
                    target=n.target,
                    events=[NotificationEvent(e) for e in n.events],
                    headers=n.headers,
                )
            )

    task = await manager.create_task(
        goal=request.goal,
        name=request.name,
        description=request.description,
        task_type=task_type,
        trigger_type=trigger,
        schedule=schedule,
        workflow_id=request.workflow_id,
        workflow_context=request.workflow_context,
        condition=condition,
        notifications=notifications,
        max_retries=request.max_retries,
        min_confidence=request.min_confidence,
        retry_policy=request.retry_policy,
        tags=request.tags,
    )

    return _task_to_response(task)


@router.post("/from-chat", response_model=ChatToTaskResponse, summary="Convert chat prompt to task")
async def create_task_from_chat(request: ChatToTaskRequest, raw_request: Request) -> ChatToTaskResponse:
    suggestion = detect_chat_task_intent(request.query)
    if not suggestion.is_task_intent:
        return ChatToTaskResponse(
            task_intent=False,
            suggested_name="",
            normalized_goal=request.query.strip(),
            trigger_type=TriggerType.MANUAL.value,
            interval_seconds=0,
            task_id=None,
        )

    task_id: Optional[str] = None
    if request.auto_create:
        uid = require_uid(raw_request)
        manager = get_task_manager(uid)
        user_info = current_user_info(raw_request)
        user_email = str(user_info.get("email") or "").strip()
        settings_enabled = False
        profile = await _store.get("profiles", "me", user_id=uid) or {}
        profile_settings = dict(profile.get("settings") or {})
        settings_enabled = bool(profile_settings.get("notifications", False))
        if not settings_enabled:
            try:
                db = get_firestore_client()
                if db is not None:
                    doc = db.collection("users").document(uid).get()
                    if doc.exists:
                        legacy = doc.to_dict() or {}
                        legacy_settings = dict(legacy.get("settings") or {})
                        settings_enabled = bool(legacy_settings.get("notifications", False))
            except Exception:
                settings_enabled = False
        settings = get_settings()
        schedule = None
        if suggestion.trigger_type == TriggerType.TIME_BASED.value and suggestion.interval_seconds > 0:
            schedule = ScheduleConfig(
                interval_seconds=suggestion.interval_seconds,
                max_runs=suggestion.max_runs,
                run_immediately=suggestion.run_immediately,
            )
        q_lower = request.query.lower()
        reminder_like = (
            "remind" in q_lower
            or "send me" in q_lower
            or "email me" in q_lower
            or "mail me" in q_lower
            or "notify me" in q_lower
            or (
                suggestion.trigger_type == TriggerType.TIME_BASED.value
                and suggestion.interval_seconds > 0
            )
        )
        auto_notifications: List[NotificationConfig] = []
        explicit_email_intent = any(
            phrase in q_lower for phrase in ("send me", "mail me", "email me", "notify me")
        )
        if reminder_like:
            auto_notifications.append(
                NotificationConfig(
                    channel=NotificationChannel.FCM,
                    target="device",
                    events=[NotificationEvent.TASK_SUCCESS, NotificationEvent.TASK_FAILED],
                )
            )
            if user_email and (settings_enabled or explicit_email_intent):
                auto_notifications.append(
                    NotificationConfig(
                        channel=NotificationChannel.EMAIL,
                        target=user_email,
                        events=[NotificationEvent.TASK_SUCCESS, NotificationEvent.TASK_FAILED],
                    )
                )
            if settings.default_whatsapp_target:
                auto_notifications.append(
                    NotificationConfig(
                        channel=NotificationChannel.WHATSAPP,
                        target=settings.default_whatsapp_target,
                        events=[NotificationEvent.TASK_SUCCESS, NotificationEvent.TASK_FAILED],
                    )
                )
            if settings.telegram_chat_id:
                auto_notifications.append(
                    NotificationConfig(
                        channel=NotificationChannel.TELEGRAM,
                        target=settings.telegram_chat_id,
                        events=[NotificationEvent.TASK_SUCCESS, NotificationEvent.TASK_FAILED],
                    )
                )
        created = await manager.create_task(
            goal=suggestion.normalized_goal,
            name=request.name or suggestion.task_name,
            task_type=TaskType.SIMPLE,
            trigger_type=TriggerType(suggestion.trigger_type),
            schedule=schedule,
            notifications=auto_notifications,
            tags=request.tags,
        )
        task_id = created.task_id

    return ChatToTaskResponse(
        task_intent=True,
        suggested_name=suggestion.task_name,
        normalized_goal=suggestion.normalized_goal,
        trigger_type=suggestion.trigger_type,
        interval_seconds=suggestion.interval_seconds,
        task_id=task_id,
    )


@router.get("", response_model=List[TaskResponse], summary="List all tasks")
async def list_tasks(
    raw_request: Request,
    status: Optional[str] = None,
    tag: Optional[str] = None,
) -> List[TaskResponse]:
    """List tasks with optional filtering."""
    manager = get_task_manager(require_uid(raw_request))
    task_status = TaskStatus(status) if status else None
    tasks = await manager.list_tasks(status=task_status, tag=tag)
    return [_task_to_response(t) for t in tasks]


@router.get("/stats", summary="Get task system stats")
async def get_stats(raw_request: Request) -> Dict[str, Any]:
    """Get task system statistics."""
    return await get_task_manager(require_uid(raw_request)).stats()


@router.get("/scheduler/status", summary="Get scheduler status")
async def get_scheduler_status(raw_request: Request) -> Dict[str, Any]:
    _ = require_uid(raw_request)
    return scheduler_stats()


@router.get("/{task_id}", response_model=TaskResponse, summary="Get task details")
async def get_task(task_id: str, raw_request: Request) -> TaskResponse:
    """Get details for a specific task."""
    task = await get_task_manager(require_uid(raw_request)).get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return _task_to_response(task)


@router.post("/{task_id}/run", response_model=ExecutionResponse, summary="Run task now")
async def run_task(task_id: str, raw_request: Request) -> ExecutionResponse:
    """Execute a task immediately."""
    manager = get_task_manager(require_uid(raw_request))
    task = await manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    try:
        execution = await manager.execute_task(task_id)
        return ExecutionResponse(
            execution_id=execution.execution_id,
            success=execution.success,
            result=str(execution.result)[:1000] if execution.result else None,
            error=execution.error,
            confidence=execution.confidence,
            elapsed_ms=execution.elapsed_ms,
            attempts=execution.attempts,
            retry_count=execution.retry_count,
            failure_type=execution.failure_type,
            confidence_gate=execution.confidence_gate,
            condition_met=execution.condition_met,
            action_taken=execution.action_taken,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/pause", summary="Pause a task")
async def pause_task(task_id: str, raw_request: Request) -> Dict[str, str]:
    """Pause a running/active task."""
    manager = get_task_manager(require_uid(raw_request))
    task = await manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=400, detail="Cannot pause task")
    task.pause()
    await manager.update_task_status(task_id, task.status)
    return {"status": "paused", "task_id": task_id}


@router.post("/{task_id}/resume", summary="Resume a task")
async def resume_task(task_id: str, raw_request: Request) -> Dict[str, str]:
    """Resume a paused task."""
    manager = get_task_manager(require_uid(raw_request))
    task = await manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=400, detail="Cannot resume task")
    task.resume()
    await manager.update_task_status(task_id, task.status)
    return {"status": "resumed", "task_id": task_id}


@router.delete("/{task_id}", summary="Delete a task")
async def delete_task(task_id: str, raw_request: Request) -> Dict[str, str]:
    """Delete a task."""
    manager = get_task_manager(require_uid(raw_request))
    if await manager.delete_task(task_id):
        return {"status": "deleted", "task_id": task_id}
    raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")


@router.get("/{task_id}/history", summary="Get execution history")
async def get_history(task_id: str, raw_request: Request, limit: int = 10) -> List[Dict[str, Any]]:
    """Get execution history for a task."""
    history = await get_task_manager(require_uid(raw_request)).get_history(task_id, limit=limit)
    if history is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return history


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def _task_to_response(task) -> TaskResponse:
    return TaskResponse(
        task_id=task.task_id,
        name=task.name,
        goal=task.goal,
        status=task.status.value,
        task_type=task.task_type.value,
        trigger_type=task.trigger_type.value,
        workflow_id=task.workflow_id,
        is_recurring=task.is_recurring,
        run_count=task.run_count,
        success_count=task.success_count,
        last_result=task.last_result.to_dict() if task.last_result else None,
        last_run_at=float(getattr(task, "last_run_at", 0.0) or 0.0),
        created_at=task.created_at,
        next_run_at=task.next_run_at,
        total_cost=task.total_cost,
        max_retries=int(getattr(task, "max_retries", 3) or 3),
        min_confidence=float(getattr(task, "min_confidence", 0.0) or 0.0),
        retry_policy=dict(getattr(task, "retry_policy", {}) or {}),
        tags=task.tags,
    )
