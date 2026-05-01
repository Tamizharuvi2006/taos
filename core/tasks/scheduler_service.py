"""Persistent task scheduler service for API runtime."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional

from taos.config.settings import get_settings
from taos.core.tasks.persistent_manager import PersistentTaskManager
from taos.infra.logging.logger import TAOSLogger


ManagerProvider = Callable[[], Dict[str, PersistentTaskManager]]


class PersistentTaskScheduler:
    """Polls all active user task managers and runs due tasks."""

    def __init__(
        self,
        get_managers: ManagerProvider,
        poll_seconds: int = 10,
        max_concurrent_runs: int = 10,
        logger: Optional[TAOSLogger] = None,
    ) -> None:
        self._get_managers = get_managers
        self._poll_seconds = poll_seconds
        self._max_concurrent_runs = max_concurrent_runs
        self._logger = logger or TAOSLogger(name="taos.ptask.scheduler")
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._inflight: set[str] = set()
        self._settings = get_settings()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        self._logger.info("ptask.scheduler_started", poll_seconds=self._poll_seconds)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._logger.info("ptask.scheduler_stopped")

    async def run_cycle(self) -> int:
        """Run one scheduler cycle (handy for tests/manual trigger)."""
        managers = self._get_managers()
        if not managers:
            return 0

        jobs: List[Awaitable[None]] = []
        for user_id, manager in managers.items():
            due_tasks = await manager.get_due_tasks()
            for task in due_tasks:
                if len(jobs) >= self._max_concurrent_runs:
                    break
                if task.task_id in self._inflight:
                    continue
                jobs.append(self._execute_task(user_id, manager, task.task_id))
            if len(jobs) >= self._max_concurrent_runs:
                break

        if not jobs:
            return 0
        await asyncio.gather(*jobs, return_exceptions=True)
        return len(jobs)

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.run_cycle()
            except Exception as exc:
                self._logger.error("ptask.scheduler_cycle_failed", error=str(exc))
            await asyncio.sleep(self._poll_seconds)

    async def _execute_task(self, user_id: str, manager: PersistentTaskManager, task_id: str) -> None:
        self._inflight.add(task_id)
        try:
            await asyncio.wait_for(
                manager.execute_task(task_id),
                timeout=float(self._settings.max_request_time_seconds),
            )
            self._logger.info("ptask.scheduler_executed", user_id=user_id, task_id=task_id)
        except asyncio.TimeoutError:
            self._logger.warning("ptask.scheduler_timeout", user_id=user_id, task_id=task_id)
        except Exception as exc:
            self._logger.error(
                "ptask.scheduler_execute_failed",
                user_id=user_id,
                task_id=task_id,
                error=str(exc),
            )
        finally:
            self._inflight.discard(task_id)

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "poll_seconds": self._poll_seconds,
            "max_concurrent_runs": self._max_concurrent_runs,
            "inflight_count": len(self._inflight),
            "known_users": len(self._get_managers()),
        }
