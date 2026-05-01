import pytest

from taos.core.tasks.scheduler_service import PersistentTaskScheduler


class _FakeTask:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id


class _FakeManager:
    def __init__(self) -> None:
        self.executed = []
        self._due = [_FakeTask("t1"), _FakeTask("t2")]

    async def get_due_tasks(self):
        return list(self._due)

    async def execute_task(self, task_id: str):
        self.executed.append(task_id)
        return {"ok": True}


@pytest.mark.asyncio
async def test_scheduler_runs_due_tasks_once():
    manager = _FakeManager()
    scheduler = PersistentTaskScheduler(
        get_managers=lambda: {"u1": manager},
        poll_seconds=5,
        max_concurrent_runs=10,
    )

    executed = await scheduler.run_cycle()
    assert executed == 2
    assert manager.executed == ["t1", "t2"]

