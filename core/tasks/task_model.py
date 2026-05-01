"""
TAOS Task Model — Persistent task structure for autonomous automation.

Every task stores:
- task_id: Unique identifier
- goal: What to accomplish
- schedule: When to run (cron-like or interval)
- condition: Optional if/else logic
- last_result: Most recent execution result
- status: active | paused | completed | failed
- trigger_type: manual | time_based | event_based

This is the core data model for the AI Automation Agent.
"""

from __future__ import annotations

import uuid
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from taos.core.notifications.models import NotificationConfig


class TaskStatus(str, Enum):
    """Task lifecycle status."""
    PENDING = "pending"        # Created, not yet run
    ACTIVE = "active"          # Scheduled and running
    PAUSED = "paused"          # Temporarily paused
    RUNNING = "running"        # Currently executing
    COMPLETED = "completed"    # Final: finished successfully
    FAILED = "failed"          # Final: gave up after retries


class TriggerType(str, Enum):
    """How the task gets triggered."""
    MANUAL = "manual"          # User triggers explicitly
    TIME_BASED = "time_based"  # Cron / interval schedule
    EVENT_BASED = "event_based"  # Change detection triggers


class TaskType(str, Enum):
    """Task execution mode."""
    SIMPLE = "simple"
    WORKFLOW = "workflow"


class ScheduleConfig:
    """Schedule configuration for recurring tasks."""

    def __init__(
        self,
        interval_seconds: int = 0,
        cron_expression: str = "",
        max_runs: int = 0,          # 0 = unlimited
        run_immediately: bool = True,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.cron_expression = cron_expression
        self.max_runs = max_runs
        self.run_immediately = run_immediately

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interval_seconds": self.interval_seconds,
            "cron_expression": self.cron_expression,
            "max_runs": self.max_runs,
            "run_immediately": self.run_immediately,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduleConfig":
        return cls(**data)

    @classmethod
    def once(cls) -> "ScheduleConfig":
        return cls(max_runs=1, run_immediately=True)

    @classmethod
    def every(cls, seconds: int) -> "ScheduleConfig":
        return cls(interval_seconds=seconds, run_immediately=True)

    @classmethod
    def daily(cls) -> "ScheduleConfig":
        return cls(interval_seconds=86400, run_immediately=True)

    @classmethod
    def hourly(cls) -> "ScheduleConfig":
        return cls(interval_seconds=3600, run_immediately=True)


@dataclass
class ConditionConfig:
    """Condition for conditional automation (if/else logic)."""

    field: str = ""             # Field to check in result (e.g., "price", "status")
    operator: str = ""          # "lt", "gt", "eq", "ne", "contains", "changed"
    threshold: Any = None       # Value to compare against
    action_on_true: str = ""    # What to do if condition met (e.g., "notify", "log")
    action_on_false: str = ""   # What to do if condition not met

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "operator": self.operator,
            "threshold": self.threshold,
            "action_on_true": self.action_on_true,
            "action_on_false": self.action_on_false,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConditionConfig":
        return cls(**data)


@dataclass
class TaskExecution:
    """Record of a single task execution."""

    execution_id: str = ""
    timestamp: float = 0.0
    success: bool = False
    result: Any = None
    error: Optional[str] = None
    confidence: float = 0.0
    cost: float = 0.0
    elapsed_ms: float = 0.0
    attempts: int = 1
    retry_count: int = 0
    failure_type: str = ""
    confidence_gate: float = 0.0
    condition_met: Optional[bool] = None  # None if no condition
    action_taken: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "timestamp": self.timestamp,
            "success": self.success,
            "result": str(self.result)[:500] if self.result else None,
            "error": self.error,
            "confidence": self.confidence,
            "cost": self.cost,
            "elapsed_ms": self.elapsed_ms,
            "attempts": self.attempts,
            "retry_count": self.retry_count,
            "failure_type": self.failure_type,
            "confidence_gate": self.confidence_gate,
            "condition_met": self.condition_met,
            "action_taken": self.action_taken,
        }


@dataclass
class Task:
    """
    Core task model for the AI Automation Agent.

    Represents a single automated task that can be scheduled,
    executed, and monitored.
    """

    # Identity
    task_id: str = ""
    name: str = ""
    goal: str = ""
    description: str = ""
    task_type: TaskType = TaskType.SIMPLE

    # Scheduling
    trigger_type: TriggerType = TriggerType.MANUAL
    schedule: Optional[ScheduleConfig] = None
    workflow_id: str = ""
    workflow_context: Dict[str, Any] = field(default_factory=dict)

    # Conditions & Notifications
    condition: Optional[ConditionConfig] = None
    notifications: List[NotificationConfig] = field(default_factory=list)

    # State
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = 0.0
    updated_at: float = 0.0
    last_run_at: float = 0.0
    next_run_at: float = 0.0
    run_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    total_cost: float = 0.0

    # Results
    last_result: Optional[TaskExecution] = None
    execution_history: List[TaskExecution] = field(default_factory=list)

    # Config
    max_retries: int = 3
    timeout_seconds: int = 300
    min_confidence: float = 0.0
    retry_policy: Dict[str, int] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.task_id:
            self.task_id = f"task_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = time.time()

    @property
    def is_recurring(self) -> bool:
        return (
            self.schedule is not None
            and self.schedule.interval_seconds > 0
            and (self.schedule.max_runs == 0 or self.run_count < self.schedule.max_runs)
        )

    @property
    def is_runnable(self) -> bool:
        return self.status in (TaskStatus.PENDING, TaskStatus.ACTIVE)

    @property
    def is_due(self) -> bool:
        if not self.is_runnable:
            return False
        if self.next_run_at == 0:
            return True  # Never run, run now
        return time.time() >= self.next_run_at

    def record_execution(self, execution: TaskExecution) -> None:
        """Record a task execution."""
        self.last_result = execution
        self.execution_history.append(execution)
        self.run_count += 1
        self.last_run_at = execution.timestamp
        self.total_cost += execution.cost
        self.updated_at = time.time()

        if execution.success:
            self.success_count += 1
        else:
            self.fail_count += 1

        # Update next run time
        if self.is_recurring and self.schedule:
            self.next_run_at = time.time() + self.schedule.interval_seconds
            self.status = TaskStatus.ACTIVE
        elif self.schedule and self.schedule.max_runs > 0 and self.run_count >= self.schedule.max_runs:
            self.status = TaskStatus.COMPLETED
        elif not self.is_recurring:
            self.status = TaskStatus.COMPLETED

    def pause(self) -> None:
        self.status = TaskStatus.PAUSED
        self.updated_at = time.time()

    def resume(self) -> None:
        self.status = TaskStatus.ACTIVE
        self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "goal": self.goal,
            "description": self.description,
            "task_type": self.task_type.value,
            "trigger_type": self.trigger_type.value,
            "schedule": self.schedule.to_dict() if self.schedule else None,
            "workflow_id": self.workflow_id,
            "workflow_context": self.workflow_context,
            "condition": self.condition.to_dict() if self.condition else None,
            "notifications": [n.to_dict() for n in self.notifications],
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "run_count": self.run_count,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "total_cost": self.total_cost,
            "last_result": self.last_result.to_dict() if self.last_result else None,
            "is_recurring": self.is_recurring,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "min_confidence": self.min_confidence,
            "retry_policy": self.retry_policy,
            "tags": self.tags,
        }
