"""
TAOS Persistent Task Manager — Firebase-backed task management.

Wraps TaskManager with persistent storage via StorageBackend.
Falls back to in-memory if Firebase is unavailable.

Usage:
    manager = PersistentTaskManager(user_id="user123")
    task = await manager.create_task(goal="Check Python version daily")
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from taos.core.tasks.task_model import (
    Task,
    TaskType,
    TaskStatus,
    TaskExecution,
    TriggerType,
    ScheduleConfig,
    ConditionConfig,
)
from taos.core.tasks.condition_checker import ConditionChecker
from taos.core.notifications.notifier import NotificationManager
from taos.core.notifications.models import NotificationEvent, NotificationConfig, NotificationChannel
from taos.infra.persistence.store import StorageBackend
from taos.infra.persistence.shared_store import get_shared_store
from taos.infra.logging.logger import TAOSLogger
from taos.config.settings import get_settings


TASKS_COLLECTION = "tasks"
EXECUTIONS_COLLECTION = "executions"
NOTIFICATIONS_COLLECTION = "notifications"

DEFAULT_RETRY_POLICY: Dict[str, int] = {
    "timeout": 2,
    "network": 2,
    "rate_limit": 3,
    "service_unavailable": 2,
    "low_confidence": 1,
    "runtime": 0,
    "auth": 0,
    "validation": 0,
}


class PersistentTaskManager:
    """
    Task manager with persistent storage (Firebase/in-memory).

    All operations are user-scoped via user_id.
    """

    def __init__(
        self,
        store: Optional[StorageBackend] = None,
        user_id: str = "default",
        logger: Optional[TAOSLogger] = None,
    ) -> None:
        if store is not None:
            self._store = store
        else:
            self._store = get_shared_store()
        self._user_id = user_id
        self._condition_checker = ConditionChecker()
        self._notifier = NotificationManager(store=self._store)
        self._logger = logger or TAOSLogger(name="taos.ptasks")
        self._settings = get_settings()

    # ═══════════════════════════════════════════════════════════
    # CRUD
    # ═══════════════════════════════════════════════════════════

    async def create_task(
        self,
        goal: str,
        name: str = "",
        description: str = "",
        task_type: TaskType = TaskType.SIMPLE,
        trigger_type: TriggerType = TriggerType.MANUAL,
        schedule: Optional[ScheduleConfig] = None,
        workflow_id: str = "",
        workflow_context: Optional[Dict[str, Any]] = None,
        condition: Optional[ConditionConfig] = None,
        notifications: Optional[List[Any]] = None,
        max_retries: int = 3,
        min_confidence: float = 0.0,
        retry_policy: Optional[Dict[str, int]] = None,
        tags: Optional[List[str]] = None,
    ) -> Task:
        """Create and persist a new task."""
        task = Task(
            name=name or goal[:50],
            goal=goal,
            description=description,
            task_type=task_type,
            trigger_type=trigger_type,
            schedule=schedule,
            workflow_id=workflow_id or "",
            workflow_context=workflow_context or {},
            condition=condition,
            notifications=notifications or [],
            max_retries=max(0, int(max_retries)),
            min_confidence=max(0.0, min(1.0, float(min_confidence or 0.0))),
            retry_policy=self._normalize_retry_policy(retry_policy or {}),
            status=TaskStatus.ACTIVE if schedule else TaskStatus.PENDING,
            tags=tags or [],
        )

        if schedule and schedule.run_immediately:
            task.next_run_at = 0
        elif schedule and schedule.interval_seconds > 0:
            task.next_run_at = time.time() + schedule.interval_seconds

        # Persist
        await self._store.set(
            TASKS_COLLECTION,
            task.task_id,
            task.to_dict(),
            user_id=self._user_id,
        )

        self._logger.info("ptask.created", task_id=task.task_id, user_id=self._user_id)
        return task

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task from storage."""
        data = await self._store.get(TASKS_COLLECTION, task_id, user_id=self._user_id)
        if not data:
            return None
        return self._dict_to_task(data)

    async def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        tag: Optional[str] = None,
        limit: int = 50,
    ) -> List[Task]:
        """List tasks from storage."""
        filters = {}
        if status:
            filters["status"] = status.value

        docs = await self._store.list(
            TASKS_COLLECTION,
            user_id=self._user_id,
            filters=filters if filters else None,
            limit=limit,
        )

        tasks = [self._dict_to_task(d) for d in docs]
        if tag:
            tasks = [t for t in tasks if tag in t.tags]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task from storage."""
        result = await self._store.delete(TASKS_COLLECTION, task_id, user_id=self._user_id)
        if result:
            self._logger.info("ptask.deleted", task_id=task_id)
        return result

    async def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        """Update task status in storage."""
        await self._store.update(
            TASKS_COLLECTION,
            task_id,
            {"status": status.value, "updated_at": time.time()},
            user_id=self._user_id,
        )

    # ═══════════════════════════════════════════════════════════
    # EXECUTION
    # ═══════════════════════════════════════════════════════════

    async def execute_task(self, task_id: str) -> TaskExecution:
        """Execute a task and persist results."""
        task = await self.get_task(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")
        # Migration guard: old delayed-only tasks may exist with unlimited runs.
        # Force them into one-time mode before execution.
        if (
            self._is_delayed_only_goal(task.goal)
            and task.schedule is not None
            and task.schedule.max_runs == 0
        ):
            task.schedule.max_runs = 1
        if not task.is_runnable:
            raise ValueError(f"Task not runnable: {task.status.value}")

        # Mark running
        await self.update_task_status(task_id, TaskStatus.RUNNING)
        execution_id = f"exec_{uuid.uuid4().hex[:8]}"
        start_time = time.time()
        attempts = 0
        retry_count = 0
        failure_type = ""
        failure_counts: Dict[str, int] = {}
        raw_result: Dict[str, Any] = {}
        retry_policy = self._effective_retry_policy(task)
        confidence_gate = self._resolve_confidence_gate(task)

        self._logger.info("ptask.executing", task_id=task_id, execution_id=execution_id)

        try:
            while True:
                attempts += 1
                run_request_id = execution_id if attempts == 1 else f"{execution_id}_r{attempts}"
                raw_result = await self._run_task_once(task=task, request_id=run_request_id)

                # For price-monitor reminder goals, add an explicit higher/lower/same decision.
                raw_result = self._enrich_price_direction_result(raw_result, task)

                attempt_success, failure_type, normalized_error = self._evaluate_attempt(
                    raw_result=raw_result,
                    confidence_gate=confidence_gate,
                )

                if attempt_success:
                    raw_result["success"] = True
                    break

                raw_result["success"] = False
                raw_result["error"] = normalized_error
                raw_result["failure_type"] = failure_type

                if not self._can_retry(
                    task=task,
                    failure_type=failure_type,
                    retry_count=retry_count,
                    failure_counts=failure_counts,
                    retry_policy=retry_policy,
                ):
                    break

                retry_count += 1
                failure_counts[failure_type] = failure_counts.get(failure_type, 0) + 1
                backoff_seconds = self._retry_backoff_seconds(
                    failure_type=failure_type,
                    retry_index=failure_counts[failure_type],
                )
                self._logger.warning(
                    "ptask.retry_scheduled",
                    task_id=task_id,
                    execution_id=execution_id,
                    failure_type=failure_type,
                    retry_count=retry_count,
                    backoff_seconds=backoff_seconds,
                )
                if backoff_seconds > 0:
                    await asyncio.sleep(backoff_seconds)

            elapsed_ms = (time.time() - start_time) * 1000

            # Evaluate condition
            condition_met = None
            action_taken = ""
            if task.condition and raw_result.get("success", False):
                prev = task.last_result.result if task.last_result else None
                condition_met, explanation = self._condition_checker.evaluate(
                    task.condition, raw_result.get("result"), prev
                )
                action_taken = (
                    task.condition.action_on_true if condition_met
                    else task.condition.action_on_false
                ) or "logged"

            execution = TaskExecution(
                execution_id=execution_id,
                timestamp=time.time(),
                success=raw_result.get("success", False),
                result=raw_result.get("formatted_response") or raw_result.get("result"),
                error=raw_result.get("error"),
                confidence=raw_result.get("confidence", 0.0),
                cost=raw_result.get("total_cost", 0.0),
                elapsed_ms=elapsed_ms,
                attempts=max(1, attempts),
                retry_count=max(0, retry_count),
                failure_type=failure_type,
                confidence_gate=confidence_gate,
                condition_met=condition_met,
                action_taken=action_taken,
            )

        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            execution = TaskExecution(
                execution_id=execution_id,
                timestamp=time.time(),
                success=False,
                error=str(e),
                elapsed_ms=elapsed_ms,
                attempts=max(1, attempts),
                retry_count=max(0, retry_count),
                failure_type=self._classify_failure_type(str(e)),
                confidence_gate=confidence_gate,
            )

        # Record and persist
        task.record_execution(execution)

        # Guardrail for existing one-time reminder tasks that were created as infinite loops.
        threshold = 1 if self._is_delayed_only_goal(task.goal) else 2
        if self._is_one_time_followup_goal(task.goal) and task.run_count >= threshold:
            task.status = TaskStatus.COMPLETED
            task.next_run_at = 0.0

        # Auto-heal old tasks created without notification config (e.g. "send me after 30 sec ...").
        if not task.notifications:
            implicit_notifications = await self._build_implicit_notifications(task)
            if implicit_notifications:
                task.notifications = implicit_notifications

        await self._store.set(
            TASKS_COLLECTION, task_id, task.to_dict(), user_id=self._user_id
        )

        # Also persist execution separately for history
        await self._store.set(
            EXECUTIONS_COLLECTION,
            execution_id,
            {**execution.to_dict(), "task_id": task_id},
            user_id=self._user_id,
        )

        # Always record an in-app reminder entry for UI feeds.
        in_app_id = f"notif_{task.task_id}_{execution.execution_id}_in_app"
        await self._store.set(
            NOTIFICATIONS_COLLECTION,
            in_app_id,
            {
                "id": in_app_id,
                "channel": "in_app",
                "target": self._user_id,
                "event": "task_success" if execution.success else "task_failed",
                "status": "delivered",
                "task_id": task.task_id,
                "task_name": task.name,
                "execution_id": execution.execution_id,
                "message": self._compact_notification_message(execution),
                "sent_at": time.time(),
                "next_run_at": float(task.next_run_at or 0.0),
            },
            user_id=self._user_id,
        )

        self._logger.info(
            "ptask.executed",
            task_id=task_id,
            success=execution.success,
            elapsed_ms=round(elapsed_ms, 2),
            attempts=execution.attempts,
            retry_count=execution.retry_count,
            failure_type=execution.failure_type or "none",
        )

        # ─── DISPATCH NOTIFICATIONS ───
        if execution.success:
            self._notifier.dispatch(task, execution, NotificationEvent.TASK_SUCCESS, user_id=self._user_id)
        else:
            self._notifier.dispatch(task, execution, NotificationEvent.TASK_FAILED, user_id=self._user_id)

        if condition_met:
            self._notifier.dispatch(task, execution, NotificationEvent.CONDITION_MET, user_id=self._user_id)

        return execution

    async def _build_implicit_notifications(self, task: Task) -> List[NotificationConfig]:
        text = (task.goal or "").lower()
        asks_to_send = any(p in text for p in ("send me", "email me", "mail me", "notify me"))
        if not asks_to_send:
            return []
        profile = await self._store.get("profiles", "me", user_id=self._user_id) or {}
        email = str(profile.get("email") or "").strip()
        if not email:
            return []
        return [
            NotificationConfig(
                channel=NotificationChannel.EMAIL,
                target=email,
                events=[NotificationEvent.TASK_SUCCESS, NotificationEvent.TASK_FAILED],
            )
        ]

    async def _run_task_once(self, task: Task, request_id: str) -> Dict[str, Any]:
        """Run one task execution attempt."""
        if task.task_type == TaskType.WORKFLOW or task.workflow_id:
            if not task.workflow_id:
                raise ValueError("Workflow task requires workflow_id")
            from taos.core.workflows.persistent_manager import PersistentWorkflowManager

            workflow_manager = PersistentWorkflowManager(
                user_id=self._user_id,
                store=self._store,
                logger=self._logger,
            )
            run = await workflow_manager.run_workflow(
                task.workflow_id,
                initial_context=task.workflow_context or {},
            )
            return {
                "success": run.status.value == "success",
                "result": {
                    "workflow_run_id": run.run_id,
                    "workflow_id": run.workflow_id,
                    "node_status": run.node_status,
                    "context": run.context,
                },
                "formatted_response": f"Workflow {run.workflow_id} finished with status {run.status.value}",
                "error": run.error or None,
                "confidence": 0.9 if run.status.value == "success" else 0.2,
                "total_cost": 0.0,
            }

        from taos.orchestration.engine import OrchestrationEngine

        engine = OrchestrationEngine(logger=self._logger)
        return await engine.run(goal=task.goal, request_id=request_id)

    def _resolve_confidence_gate(self, task: Task) -> float:
        configured = float(task.min_confidence or 0.0)
        if configured > 0:
            return max(0.0, min(1.0, configured))
        return max(0.0, min(1.0, float(self._settings.confidence_retry_threshold)))

    def _normalize_retry_policy(self, retry_policy: Dict[str, int]) -> Dict[str, int]:
        cleaned: Dict[str, int] = {}
        for key, value in (retry_policy or {}).items():
            k = str(key or "").strip().lower()
            if not k:
                continue
            try:
                cleaned[k] = max(0, int(value))
            except Exception:
                continue
        return cleaned

    def _effective_retry_policy(self, task: Task) -> Dict[str, int]:
        merged = dict(DEFAULT_RETRY_POLICY)
        merged.update(self._normalize_retry_policy(task.retry_policy or {}))
        return merged

    def _evaluate_attempt(self, raw_result: Dict[str, Any], confidence_gate: float) -> tuple[bool, str, str]:
        success = bool(raw_result.get("success", False))
        confidence = float(raw_result.get("confidence", 0.0) or 0.0)
        error = str(raw_result.get("error") or "").strip()

        if success and confidence_gate > 0 and confidence > 0 and confidence < confidence_gate:
            message = (
                f"Confidence gate blocked this run "
                f"({confidence:.2f} < required {confidence_gate:.2f})."
            )
            return False, "low_confidence", message

        if success:
            return True, "", ""

        if not error:
            error = "Task execution failed without error details."
        return False, self._classify_failure_type(error), error

    def _classify_failure_type(self, error_text: str) -> str:
        text = str(error_text or "").lower()
        if not text:
            return "runtime"
        if any(k in text for k in ("timeout", "timed out", "deadline exceeded")):
            return "timeout"
        if any(k in text for k in ("rate limit", "429", "too many requests")):
            return "rate_limit"
        if any(
            k in text
            for k in (
                "network",
                "connection",
                "host",
                "dns",
                "socket",
                "unreachable",
                "connection reset",
            )
        ):
            return "network"
        if any(k in text for k in ("unauthorized", "forbidden", "permission", "auth", "401", "403")):
            return "auth"
        if any(k in text for k in ("validation", "invalid", "bad request", "400", "valueerror")):
            return "validation"
        if any(k in text for k in ("service unavailable", "gateway", "502", "503", "overloaded")):
            return "service_unavailable"
        return "runtime"

    def _can_retry(
        self,
        task: Task,
        failure_type: str,
        retry_count: int,
        failure_counts: Dict[str, int],
        retry_policy: Dict[str, int],
    ) -> bool:
        if retry_count >= max(0, int(task.max_retries)):
            return False
        if not failure_type:
            return False
        allowed_for_type = max(0, int(retry_policy.get(failure_type, 0)))
        used_for_type = max(0, int(failure_counts.get(failure_type, 0)))
        return used_for_type < allowed_for_type

    def _retry_backoff_seconds(self, failure_type: str, retry_index: int) -> float:
        base = {
            "rate_limit": 0.25,
            "service_unavailable": 0.2,
            "network": 0.15,
            "timeout": 0.1,
            "low_confidence": 0.05,
            "runtime": 0.05,
        }.get(failure_type, 0.05)
        return round(min(1.0, base * max(1, retry_index)), 3)

    def _compact_notification_message(self, execution: TaskExecution) -> str:
        text = str(execution.result or execution.error or "Task update")
        text = text.replace("\r", " ").replace("\n", " ")
        text = " ".join(text.split())
        if len(text) > 160:
            text = text[:157].rstrip() + "..."
        return text

    async def get_history(self, task_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get execution history for a task."""
        docs = await self._store.list(
            EXECUTIONS_COLLECTION,
            user_id=self._user_id,
            filters={"task_id": task_id},
            limit=limit,
        )
        return sorted(docs, key=lambda d: d.get("timestamp", 0), reverse=True)

    async def list_notifications(
        self,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        filters = {"status": status} if status else None
        docs = await self._store.list(
            NOTIFICATIONS_COLLECTION,
            user_id=self._user_id,
            filters=filters,
            limit=limit,
        )
        return sorted(docs, key=lambda d: d.get("sent_at", 0), reverse=True)[:limit]

    async def get_due_tasks(self) -> List[Task]:
        """Get all tasks due for execution."""
        all_tasks = await self.list_tasks()
        due: List[Task] = []
        for t in all_tasks:
            # Migration guard: ensure delayed-only reminders are one-time.
            if (
                self._is_delayed_only_goal(t.goal)
                and t.schedule is not None
                and t.schedule.max_runs == 0
            ):
                t.schedule.max_runs = 1
                await self._store.set(
                    TASKS_COLLECTION,
                    t.task_id,
                    t.to_dict(),
                    user_id=self._user_id,
                )
            # Auto-heal previously created looping one-time reminders.
            threshold = 1 if self._is_delayed_only_goal(t.goal) else 2
            if self._is_one_time_followup_goal(t.goal) and t.run_count >= threshold:
                if t.status != TaskStatus.COMPLETED:
                    t.status = TaskStatus.COMPLETED
                    t.next_run_at = 0.0
                    await self._store.set(
                        TASKS_COLLECTION,
                        t.task_id,
                        t.to_dict(),
                        user_id=self._user_id,
                    )
                continue
            if t.is_due:
                due.append(t)
        return due

    # ═══════════════════════════════════════════════════════════
    # STATS
    # ═══════════════════════════════════════════════════════════

    async def stats(self) -> Dict[str, Any]:
        """Get task system stats."""
        tasks = await self.list_tasks(limit=500)
        return {
            "user_id": self._user_id,
            "total_tasks": len(tasks),
            "active": sum(1 for t in tasks if t.status == TaskStatus.ACTIVE),
            "paused": sum(1 for t in tasks if t.status == TaskStatus.PAUSED),
            "completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
            "running": sum(1 for t in tasks if t.status == TaskStatus.RUNNING),
            "recurring": sum(1 for t in tasks if t.is_recurring),
            "total_executions": sum(t.run_count for t in tasks),
            "total_cost": round(sum(t.total_cost for t in tasks), 4),
        }

    # ═══════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════

    def _dict_to_task(self, data: Dict[str, Any]) -> Task:
        """Reconstruct a Task from a dict."""
        schedule = None
        if data.get("schedule"):
            schedule = ScheduleConfig.from_dict(data["schedule"])

        condition = None
        if data.get("condition"):
            condition = ConditionConfig.from_dict(data["condition"])
            
        notifications = []
        if data.get("notifications"):
            from taos.core.notifications.models import NotificationConfig
            notifications = [NotificationConfig.from_dict(n) for n in data["notifications"]]

        last_result = None
        if data.get("last_result"):
            lr = data["last_result"]
            last_result = TaskExecution(
                execution_id=lr.get("execution_id", ""),
                timestamp=lr.get("timestamp", 0),
                success=lr.get("success", False),
                result=lr.get("result"),
                error=lr.get("error"),
                confidence=lr.get("confidence", 0),
                cost=lr.get("cost", 0),
                elapsed_ms=lr.get("elapsed_ms", 0),
                attempts=lr.get("attempts", 1),
                retry_count=lr.get("retry_count", 0),
                failure_type=lr.get("failure_type", ""),
                confidence_gate=lr.get("confidence_gate", 0.0),
                condition_met=lr.get("condition_met"),
                action_taken=lr.get("action_taken", ""),
            )

        return Task(
            task_id=data.get("task_id", ""),
            name=data.get("name", ""),
            goal=data.get("goal", ""),
            description=data.get("description", ""),
            task_type=TaskType(data.get("task_type", "simple")),
            trigger_type=TriggerType(data.get("trigger_type", "manual")),
            schedule=schedule,
            workflow_id=data.get("workflow_id", ""),
            workflow_context=data.get("workflow_context", {}) or {},
            condition=condition,
            notifications=notifications,
            status=TaskStatus(data.get("status", "pending")),
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
            last_run_at=data.get("last_run_at", 0),
            next_run_at=data.get("next_run_at", 0),
            run_count=data.get("run_count", 0),
            success_count=data.get("success_count", 0),
            fail_count=data.get("fail_count", 0),
            total_cost=data.get("total_cost", 0),
            last_result=last_result,
            max_retries=int(data.get("max_retries", 3) or 3),
            timeout_seconds=int(data.get("timeout_seconds", 300) or 300),
            min_confidence=float(data.get("min_confidence", 0.0) or 0.0),
            retry_policy=self._normalize_retry_policy(data.get("retry_policy", {}) or {}),
            tags=data.get("tags", []),
        )

    def _is_one_time_followup_goal(self, goal: str) -> bool:
        text = (goal or "").lower()
        if any(k in text for k in ("every ", "daily", "hourly", "weekly")):
            return False
        if "tomorrow" in text:
            return True
        return bool(
            re.search(
                r"(after|in)\s+(\d+)\s*(sec|secs|second|seconds|min|mins|minute|minutes|hour|hours|day|days)\b",
                text,
            )
        )

    def _is_delayed_only_goal(self, goal: str) -> bool:
        text = (goal or "").lower()
        if any(k in text for k in ("every ", "daily", "hourly", "weekly")):
            return False
        return bool(
            re.search(
                r"(after|in)\s+(\d+)\s*(sec|secs|second|seconds|min|mins|minute|minutes|hour|hours|day|days)\b",
                text,
            )
        )

    def _is_price_direction_goal(self, goal: str) -> bool:
        text = (goal or "").lower()
        direction_words = ("high", "higher", "hi", "low", "lower", "down", "up", "same", "change")
        price_words = ("price", "quote", "rate", "spot", "market")
        entities = (
            "gold", "silver", "diamond", "platinum",
            "stock", "share", "bitcoin", "btc", "ethereum", "eth", "crypto",
            "car", "vehicle", "oil",
        )
        return (
            any(w in text for w in direction_words)
            and (any(w in text for w in price_words) or any(e in text for e in entities))
        )

    def _extract_price_value(self, text: str) -> Optional[float]:
        if not text:
            return None

        # Prefer explicit dollar values.
        m = re.search(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)", text)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                return None

        # Fallback: large numeric quote patterns (avoid year/date-like small numbers).
        candidates = re.findall(r"([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?)", text)
        for c in candidates:
            try:
                v = float(c.replace(",", ""))
                if v > 1000:
                    return v
            except ValueError:
                continue
        return None

    def _enrich_price_direction_result(self, raw_result: Dict[str, Any], task: Task) -> Dict[str, Any]:
        if not self._is_price_direction_goal(task.goal):
            return raw_result

        current_text = str(raw_result.get("formatted_response") or raw_result.get("result") or "")
        current_price = self._extract_price_value(current_text)
        prev_text = str(task.last_result.result) if task.last_result and task.last_result.result else ""
        prev_price = self._extract_price_value(prev_text)

        if current_price is None:
            return raw_result

        if prev_price is None:
            trend_line = (
                f"Baseline captured at ${current_price:,.2f}. "
                "I will compare on the next reminder run."
            )
        else:
            delta = current_price - prev_price
            if delta > 0:
                direction = "HIGHER"
            elif delta < 0:
                direction = "LOWER"
            else:
                direction = "SAME"
            label = self._extract_asset_label(task.goal)
            trend_line = (
                f"Update: {label} is {direction} vs previous check. "
                f"Previous ${prev_price:,.2f} -> Current ${current_price:,.2f} "
                f"(change {delta:+,.2f})."
            )

        merged = current_text.strip()
        if merged:
            merged = f"{merged}\n\n{trend_line}"
        else:
            merged = trend_line
        raw_result["formatted_response"] = merged
        return raw_result

    def _extract_asset_label(self, goal: str) -> str:
        text = (goal or "").lower()
        if "silver" in text:
            return "Silver"
        if "diamond" in text:
            return "Diamond"
        if "platinum" in text:
            return "Platinum"
        if "bitcoin" in text or "btc" in text:
            return "Bitcoin"
        if "ethereum" in text or "eth" in text:
            return "Ethereum"
        if "stock" in text or "share" in text:
            return "Stock"
        if "car" in text or "vehicle" in text:
            return "Vehicle Price"
        if "oil" in text:
            return "Oil"
        if "gold" in text:
            return "Gold"
        return "Price"
