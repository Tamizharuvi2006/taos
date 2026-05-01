"""
TAOS Task Manager — CRUD + execution orchestration for tasks.

Manages the lifecycle of automated tasks:
- Create / Read / Update / Delete tasks
- Execute tasks through the TAOS orchestration engine
- Track execution history and results
- Evaluate conditions after execution
- Handle recurring task scheduling
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from taos.core.tasks.task_model import (
    Task,
    TaskStatus,
    TaskExecution,
    TriggerType,
    ScheduleConfig,
    ConditionConfig,
)
from taos.core.tasks.condition_checker import ConditionChecker
from taos.infra.logging.logger import TAOSLogger


class TaskManager:
    """
    Production task manager — CRUD + execution for automated tasks.

    This is the brain of the task automation system. It:
    1. Stores tasks in memory (persistent storage can be added)
    2. Executes tasks through the TAOS engine
    3. Evaluates post-execution conditions
    4. Updates task state and history
    5. Manages recurring scheduling
    """

    def __init__(self, logger: Optional[TAOSLogger] = None) -> None:
        self._tasks: Dict[str, Task] = {}
        self._condition_checker = ConditionChecker()
        self._logger = logger or TAOSLogger(name="taos.tasks")

    # ═══════════════════════════════════════════════════════════
    # CRUD OPERATIONS
    # ═══════════════════════════════════════════════════════════

    def create_task(
        self,
        goal: str,
        name: str = "",
        description: str = "",
        trigger_type: TriggerType = TriggerType.MANUAL,
        schedule: Optional[ScheduleConfig] = None,
        condition: Optional[ConditionConfig] = None,
        tags: Optional[List[str]] = None,
    ) -> Task:
        """
        Create a new automated task.

        Args:
            goal: What the task should accomplish.
            name: Human-readable task name.
            description: Optional description.
            trigger_type: How the task is triggered.
            schedule: Scheduling config for recurring tasks.
            condition: Optional condition for conditional automation.
            tags: Optional tags for organization.

        Returns:
            The created Task.
        """
        task = Task(
            name=name or goal[:50],
            goal=goal,
            description=description,
            trigger_type=trigger_type,
            schedule=schedule,
            condition=condition,
            status=TaskStatus.ACTIVE if schedule else TaskStatus.PENDING,
            tags=tags or [],
        )

        # Set initial next_run_at
        if schedule and schedule.run_immediately:
            task.next_run_at = 0  # Run immediately
        elif schedule and schedule.interval_seconds > 0:
            task.next_run_at = time.time() + schedule.interval_seconds

        self._tasks[task.task_id] = task

        self._logger.info(
            "task.created",
            task_id=task.task_id,
            goal=goal[:100],
            trigger=trigger_type.value,
            recurring=task.is_recurring,
        )

        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        tag: Optional[str] = None,
    ) -> List[Task]:
        """List tasks with optional filtering."""
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        if tag:
            tasks = [t for t in tasks if tag in t.tags]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._logger.info("task.deleted", task_id=task_id)
            return True
        return False

    def pause_task(self, task_id: str) -> bool:
        """Pause a task."""
        task = self._tasks.get(task_id)
        if task and task.status in (TaskStatus.ACTIVE, TaskStatus.PENDING):
            task.pause()
            self._logger.info("task.paused", task_id=task_id)
            return True
        return False

    def resume_task(self, task_id: str) -> bool:
        """Resume a paused task."""
        task = self._tasks.get(task_id)
        if task and task.status == TaskStatus.PAUSED:
            task.resume()
            self._logger.info("task.resumed", task_id=task_id)
            return True
        return False

    # ═══════════════════════════════════════════════════════════
    # EXECUTION
    # ═══════════════════════════════════════════════════════════

    async def execute_task(self, task_id: str) -> TaskExecution:
        """
        Execute a task through the TAOS orchestration engine.

        This is the core execution method:
        1. Run the task's goal through OrchestrationEngine
        2. Evaluate conditions (if any)
        3. Record execution results
        4. Update task state

        Returns:
            TaskExecution record.
        """
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        if not task.is_runnable:
            raise ValueError(f"Task not runnable (status: {task.status.value})")

        # Mark as running
        task.status = TaskStatus.RUNNING
        execution_id = f"exec_{uuid.uuid4().hex[:8]}"
        start_time = time.time()

        self._logger.info(
            "task.executing",
            task_id=task_id,
            execution_id=execution_id,
            run_number=task.run_count + 1,
        )

        try:
            # Import here to avoid circular imports
            from taos.orchestration.engine import OrchestrationEngine

            engine = OrchestrationEngine(logger=self._logger)
            raw_result = await engine.run(
                goal=task.goal,
                request_id=execution_id,
            )

            elapsed_ms = (time.time() - start_time) * 1000

            # Evaluate condition if present
            condition_met = None
            action_taken = ""
            if task.condition:
                prev = task.last_result.result if task.last_result else None
                condition_met, explanation = self._condition_checker.evaluate(
                    condition=task.condition,
                    current_result=raw_result.get("result"),
                    previous_result=prev,
                )
                if condition_met:
                    action_taken = task.condition.action_on_true or "condition_met"
                else:
                    action_taken = task.condition.action_on_false or "condition_not_met"

                self._logger.info(
                    "task.condition_evaluated",
                    task_id=task_id,
                    condition_met=condition_met,
                    explanation=explanation,
                    action=action_taken,
                )

            # Build execution record
            execution = TaskExecution(
                execution_id=execution_id,
                timestamp=time.time(),
                success=raw_result.get("success", False),
                result=raw_result.get("formatted_response") or raw_result.get("result"),
                error=raw_result.get("error"),
                confidence=raw_result.get("confidence", 0.0),
                cost=raw_result.get("total_cost", 0.0),
                elapsed_ms=elapsed_ms,
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
            )
            self._logger.error("task.execution_failed", task_id=task_id, error=str(e))

        # Record execution
        task.record_execution(execution)

        self._logger.info(
            "task.executed",
            task_id=task_id,
            execution_id=execution_id,
            success=execution.success,
            elapsed_ms=round(elapsed_ms, 2),
            condition_met=execution.condition_met,
            next_run_in=f"{task.schedule.interval_seconds}s" if task.is_recurring and task.schedule else "none",
        )

        return execution

    def get_due_tasks(self) -> List[Task]:
        """Get all tasks that are due for execution."""
        return [t for t in self._tasks.values() if t.is_due]

    def get_task_history(self, task_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get execution history for a task."""
        task = self._tasks.get(task_id)
        if not task:
            return []
        history = task.execution_history[-limit:]
        return [e.to_dict() for e in reversed(history)]

    # ═══════════════════════════════════════════════════════════
    # STATS
    # ═══════════════════════════════════════════════════════════

    @property
    def stats(self) -> Dict[str, Any]:
        """Get task system stats."""
        tasks = list(self._tasks.values())
        return {
            "total_tasks": len(tasks),
            "active": sum(1 for t in tasks if t.status == TaskStatus.ACTIVE),
            "paused": sum(1 for t in tasks if t.status == TaskStatus.PAUSED),
            "completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
            "failed": sum(1 for t in tasks if t.status == TaskStatus.FAILED),
            "running": sum(1 for t in tasks if t.status == TaskStatus.RUNNING),
            "recurring": sum(1 for t in tasks if t.is_recurring),
            "total_executions": sum(t.run_count for t in tasks),
            "total_cost": round(sum(t.total_cost for t in tasks), 4),
        }
