"""
TAOS Tests — Task Automation System.

Tests task model, condition checker, task manager, and scheduler.
"""

from __future__ import annotations

import time
import pytest

from taos.core.tasks.task_model import (
    Task,
    TaskStatus,
    TaskExecution,
    TriggerType,
    ScheduleConfig,
    ConditionConfig,
)
from taos.core.tasks.condition_checker import ConditionChecker
from taos.core.tasks.task_manager import TaskManager


# ═══════════════════════════════════════════════════════════
# TASK MODEL TESTS
# ═══════════════════════════════════════════════════════════

class TestTaskModel:
    def test_create_task(self):
        task = Task(goal="Search for Python updates")
        assert task.task_id.startswith("task_")
        assert task.goal == "Search for Python updates"
        assert task.status == TaskStatus.PENDING
        assert task.run_count == 0

    def test_task_id_auto_generated(self):
        t1 = Task(goal="a")
        t2 = Task(goal="b")
        assert t1.task_id != t2.task_id

    def test_is_recurring(self):
        task = Task(goal="test", schedule=ScheduleConfig.daily())
        assert task.is_recurring is True

    def test_not_recurring_without_schedule(self):
        task = Task(goal="test")
        assert task.is_recurring is False

    def test_one_shot_not_recurring(self):
        task = Task(goal="test", schedule=ScheduleConfig.once())
        assert task.is_recurring is False  # max_runs=1, run_count=0 but interval=0

    def test_is_runnable(self):
        task = Task(goal="test", status=TaskStatus.ACTIVE)
        assert task.is_runnable is True

    def test_completed_not_runnable(self):
        task = Task(goal="test", status=TaskStatus.COMPLETED)
        assert task.is_runnable is False

    def test_record_execution(self):
        task = Task(goal="test", status=TaskStatus.ACTIVE)
        execution = TaskExecution(
            execution_id="e1",
            timestamp=time.time(),
            success=True,
            result="done",
            confidence=0.9,
            cost=0.001,
        )
        task.record_execution(execution)
        assert task.run_count == 1
        assert task.success_count == 1
        assert task.last_result == execution
        assert task.total_cost == 0.001

    def test_record_failure(self):
        task = Task(goal="test", status=TaskStatus.ACTIVE)
        execution = TaskExecution(
            execution_id="e1",
            timestamp=time.time(),
            success=False,
            error="timeout",
        )
        task.record_execution(execution)
        assert task.fail_count == 1

    def test_pause_resume(self):
        task = Task(goal="test", status=TaskStatus.ACTIVE)
        task.pause()
        assert task.status == TaskStatus.PAUSED
        task.resume()
        assert task.status == TaskStatus.ACTIVE

    def test_to_dict(self):
        task = Task(goal="test", name="Test Task", tags=["monitoring"])
        d = task.to_dict()
        assert d["goal"] == "test"
        assert d["name"] == "Test Task"
        assert d["tags"] == ["monitoring"]
        assert "task_id" in d


class TestScheduleConfig:
    def test_once(self):
        s = ScheduleConfig.once()
        assert s.max_runs == 1

    def test_daily(self):
        s = ScheduleConfig.daily()
        assert s.interval_seconds == 86400

    def test_hourly(self):
        s = ScheduleConfig.hourly()
        assert s.interval_seconds == 3600

    def test_every(self):
        s = ScheduleConfig.every(300)
        assert s.interval_seconds == 300

    def test_to_dict(self):
        s = ScheduleConfig.daily()
        d = s.to_dict()
        assert d["interval_seconds"] == 86400


# ═══════════════════════════════════════════════════════════
# CONDITION CHECKER TESTS
# ═══════════════════════════════════════════════════════════

class TestConditionChecker:
    @pytest.fixture
    def checker(self):
        return ConditionChecker()

    def test_no_condition(self, checker):
        cond = ConditionConfig()
        met, explanation = checker.evaluate(cond, "anything")
        assert met is True

    def test_less_than(self, checker):
        cond = ConditionConfig(field="price", operator="lt", threshold=5000)
        met, _ = checker.evaluate(cond, {"price": 4500})
        assert met is True

    def test_less_than_fails(self, checker):
        cond = ConditionConfig(field="price", operator="lt", threshold=5000)
        met, _ = checker.evaluate(cond, {"price": 6000})
        assert met is False

    def test_greater_than(self, checker):
        cond = ConditionConfig(field="score", operator="gt", threshold=80)
        met, _ = checker.evaluate(cond, {"score": 95})
        assert met is True

    def test_equals(self, checker):
        cond = ConditionConfig(field="status", operator="eq", threshold="released")
        met, _ = checker.evaluate(cond, {"status": "Released"})
        assert met is True  # Case-insensitive

    def test_not_equals(self, checker):
        cond = ConditionConfig(field="status", operator="ne", threshold="released")
        met, _ = checker.evaluate(cond, {"status": "pending"})
        assert met is True

    def test_contains(self, checker):
        cond = ConditionConfig(field="text", operator="contains", threshold="python")
        met, _ = checker.evaluate(cond, {"text": "Python 3.13 released"})
        assert met is True

    def test_changed(self, checker):
        cond = ConditionConfig(field="version", operator="changed")
        met, _ = checker.evaluate(cond, {"version": "3.13"}, {"version": "3.12"})
        assert met is True

    def test_not_changed(self, checker):
        cond = ConditionConfig(field="version", operator="changed")
        met, _ = checker.evaluate(cond, {"version": "3.12"}, {"version": "3.12"})
        assert met is False

    def test_exists(self, checker):
        cond = ConditionConfig(field="data", operator="exists")
        met, _ = checker.evaluate(cond, {"data": "something"})
        assert met is True

    def test_not_exists(self, checker):
        cond = ConditionConfig(field="missing", operator="exists")
        met, _ = checker.evaluate(cond, {"other": "value"})
        assert met is False

    def test_nested_field(self, checker):
        cond = ConditionConfig(field="data.price", operator="lt", threshold=100)
        met, _ = checker.evaluate(cond, {"data": {"price": 50}})
        assert met is True

    def test_string_result_extraction(self, checker):
        cond = ConditionConfig(field="price", operator="lt", threshold=5000)
        met, _ = checker.evaluate(cond, "price: 4500 rupees")
        assert met is True

    def test_currency_handling(self, checker):
        cond = ConditionConfig(field="price", operator="lt", threshold=5000)
        met, _ = checker.evaluate(cond, {"price": "₹4,500"})
        assert met is True


# ═══════════════════════════════════════════════════════════
# TASK MANAGER TESTS
# ═══════════════════════════════════════════════════════════

class TestTaskManager:
    @pytest.fixture
    def manager(self):
        return TaskManager()

    def test_create_task(self, manager):
        task = manager.create_task(goal="Check Python version")
        assert task.task_id.startswith("task_")
        assert task.goal == "Check Python version"

    def test_get_task(self, manager):
        task = manager.create_task(goal="test")
        found = manager.get_task(task.task_id)
        assert found is not None
        assert found.task_id == task.task_id

    def test_get_missing_task(self, manager):
        assert manager.get_task("nonexistent") is None

    def test_list_tasks(self, manager):
        manager.create_task(goal="task 1")
        manager.create_task(goal="task 2")
        tasks = manager.list_tasks()
        assert len(tasks) == 2

    def test_list_by_status(self, manager):
        t1 = manager.create_task(goal="active", trigger_type=TriggerType.TIME_BASED,
                                  schedule=ScheduleConfig.daily())
        manager.create_task(goal="pending")
        active = manager.list_tasks(status=TaskStatus.ACTIVE)
        assert len(active) == 1

    def test_list_by_tag(self, manager):
        manager.create_task(goal="t1", tags=["monitoring"])
        manager.create_task(goal="t2", tags=["other"])
        tagged = manager.list_tasks(tag="monitoring")
        assert len(tagged) == 1

    def test_delete_task(self, manager):
        task = manager.create_task(goal="delete me")
        assert manager.delete_task(task.task_id) is True
        assert manager.get_task(task.task_id) is None

    def test_delete_nonexistent(self, manager):
        assert manager.delete_task("nope") is False

    def test_pause_task(self, manager):
        task = manager.create_task(goal="test", trigger_type=TriggerType.TIME_BASED,
                                    schedule=ScheduleConfig.daily())
        assert manager.pause_task(task.task_id) is True
        assert manager.get_task(task.task_id).status == TaskStatus.PAUSED

    def test_resume_task(self, manager):
        task = manager.create_task(goal="test", trigger_type=TriggerType.TIME_BASED,
                                    schedule=ScheduleConfig.daily())
        manager.pause_task(task.task_id)
        assert manager.resume_task(task.task_id) is True
        assert manager.get_task(task.task_id).status == TaskStatus.ACTIVE

    def test_stats(self, manager):
        manager.create_task(goal="t1", trigger_type=TriggerType.TIME_BASED,
                            schedule=ScheduleConfig.daily())
        manager.create_task(goal="t2")
        stats = manager.stats
        assert stats["total_tasks"] == 2
        assert stats["active"] >= 1

    def test_get_due_tasks(self, manager):
        task = manager.create_task(
            goal="due now",
            trigger_type=TriggerType.TIME_BASED,
            schedule=ScheduleConfig(interval_seconds=60, run_immediately=True),
        )
        due = manager.get_due_tasks()
        assert len(due) >= 1
