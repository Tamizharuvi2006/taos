"""
TAOS Task Scheduler — Background scheduler for recurring tasks.

Runs an asyncio loop that:
1. Checks for due tasks every N seconds
2. Executes them via TaskManager
3. Logs results
4. Handles failures gracefully

Trigger types supported:
- time_based: Interval-based (every N seconds)
- event_based: Placeholder for webhook/change detection
- manual: Not scheduled, only triggered via API
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from taos.core.tasks.task_manager import TaskManager
from taos.core.tasks.task_model import TaskStatus
from taos.infra.logging.logger import TAOSLogger


class TaskScheduler:
    """
    Background task scheduler.

    Runs as an asyncio task, polling for due tasks at a
    configurable interval. Executes them through TaskManager.
    """

    def __init__(
        self,
        task_manager: TaskManager,
        poll_interval: int = 30,  # seconds
        max_concurrent: int = 3,
        logger: Optional[TAOSLogger] = None,
    ) -> None:
        self._task_manager = task_manager
        self._poll_interval = poll_interval
        self._max_concurrent = max_concurrent
        self._logger = logger or TAOSLogger(name="taos.scheduler")
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._executions_count = 0
        self._start_time = 0.0

    async def start(self) -> None:
        """Start the scheduler loop."""
        if self._running:
            self._logger.warning("scheduler.already_running")
            return

        self._running = True
        self._start_time = time.time()
        self._logger.info(
            "scheduler.started",
            poll_interval=self._poll_interval,
            max_concurrent=self._max_concurrent,
        )

        self._task = asyncio.create_task(self._scheduler_loop())

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._logger.info(
            "scheduler.stopped",
            total_executions=self._executions_count,
            uptime_seconds=round(time.time() - self._start_time, 1),
        )

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop — polls for due tasks."""
        while self._running:
            try:
                await self._check_and_execute()
            except Exception as e:
                self._logger.error("scheduler.loop_error", error=str(e))

            await asyncio.sleep(self._poll_interval)

    async def _check_and_execute(self) -> None:
        """Check for due tasks and execute them."""
        due_tasks = self._task_manager.get_due_tasks()

        if not due_tasks:
            return

        self._logger.info("scheduler.due_tasks_found", count=len(due_tasks))

        # Limit concurrency
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def execute_with_semaphore(task_id: str) -> None:
            async with semaphore:
                try:
                    await self._task_manager.execute_task(task_id)
                    self._executions_count += 1
                except Exception as e:
                    self._logger.error(
                        "scheduler.task_failed",
                        task_id=task_id,
                        error=str(e),
                    )

        # Execute due tasks concurrently (up to max_concurrent)
        tasks = [
            execute_with_semaphore(task.task_id)
            for task in due_tasks[:10]  # Cap at 10 per cycle
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run_once(self) -> int:
        """Run one cycle of the scheduler (useful for testing)."""
        due_tasks = self._task_manager.get_due_tasks()
        executed = 0
        for task in due_tasks:
            try:
                await self._task_manager.execute_task(task.task_id)
                executed += 1
            except Exception as e:
                self._logger.error("scheduler.run_once_error", error=str(e))
        return executed

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "uptime_seconds": round(time.time() - self._start_time, 1) if self._start_time else 0,
            "total_executions": self._executions_count,
            "poll_interval": self._poll_interval,
            "max_concurrent": self._max_concurrent,
        }
