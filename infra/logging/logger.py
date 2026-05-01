"""
TAOS Structured Logger — Production logging with structlog.

Features:
- JSON-formatted structured logs
- Request-scoped context binding
- Log level filtering
- Step-level tracing integration
- Performance metrics in log output
"""

from __future__ import annotations

import sys
import time
import logging
from typing import Any, Optional

try:
    import structlog
    HAS_STRUCTLOG = True
except ImportError:
    HAS_STRUCTLOG = False

from taos.config.settings import get_settings


class TAOSLogger:
    """
    Production structured logger for TAOS.

    Uses structlog for JSON-formatted, context-aware logging.
    Falls back to stdlib logging if structlog is unavailable.
    """

    def __init__(
        self,
        name: str = "taos",
        request_id: Optional[str] = None,
    ) -> None:
        self._settings = get_settings()
        self._request_id = request_id
        self._start_time = time.time()

        if HAS_STRUCTLOG:
            self._configure_structlog()
            self._logger = structlog.get_logger(name)
            if request_id:
                self._logger = self._logger.bind(request_id=request_id)
        else:
            self._logger = self._configure_stdlib(name)

    def _configure_structlog(self) -> None:
        """Configure structlog with JSON processing pipeline."""
        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
        ]

        if self._settings.log_format == "json":
            processors.append(structlog.processors.JSONRenderer())
        else:
            processors.append(structlog.dev.ConsoleRenderer())

        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(
                logging.getLevelName(self._settings.log_level)
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )

    def _configure_stdlib(self, name: str) -> logging.Logger:
        """Fallback to stdlib logging."""
        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, self._settings.log_level, logging.INFO))

        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(logger.level)
            formatter = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        return logger

    # ─── Logging Methods ──────────────────────────────────

    def info(self, event: str, **kwargs: Any) -> None:
        """Log an info event."""
        self._emit("info", event, **kwargs)

    def debug(self, event: str, **kwargs: Any) -> None:
        """Log a debug event."""
        self._emit("debug", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        """Log a warning event."""
        self._emit("warning", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        """Log an error event."""
        self._emit("error", event, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> None:
        """Log a critical event."""
        self._emit("critical", event, **kwargs)

    # ─── Context Binding ──────────────────────────────────

    def bind(self, **kwargs: Any) -> "TAOSLogger":
        """Bind additional context to all subsequent log events."""
        if HAS_STRUCTLOG and hasattr(self._logger, 'bind'):
            self._logger = self._logger.bind(**kwargs)
        return self

    def with_request(self, request_id: str) -> "TAOSLogger":
        """Create a request-scoped logger."""
        new_logger = TAOSLogger.__new__(TAOSLogger)
        new_logger._settings = self._settings
        new_logger._request_id = request_id
        new_logger._start_time = time.time()

        if HAS_STRUCTLOG:
            new_logger._logger = self._logger.bind(request_id=request_id)
        else:
            new_logger._logger = self._logger

        return new_logger

    # ─── Specialized Events ───────────────────────────────

    def step_start(self, step_id: str, step_index: int, action: str, tool: Optional[str] = None) -> None:
        self.info(
            "step.start",
            step_id=step_id,
            step_index=step_index,
            action=action[:100],
            tool=tool,
        )

    def step_complete(self, step_id: str, success: bool, latency: float, cost: float) -> None:
        self.info(
            "step.complete",
            step_id=step_id,
            success=success,
            latency_ms=round(latency * 1000, 2),
            cost=cost,
        )

    def transition(self, from_state: str, to_state: str, version: int) -> None:
        self.info(
            "fsm.transition",
            from_state=from_state,
            to_state=to_state,
            state_version=version,
        )

    def task_complete(self, success: bool, status: str, steps: int, cost: float) -> None:
        elapsed = time.time() - self._start_time
        self.info(
            "task.complete",
            success=success,
            status=status,
            steps_executed=steps,
            total_cost=cost,
            elapsed_seconds=round(elapsed, 2),
        )

    # ─── Internal ─────────────────────────────────────────

    def _emit(self, level: str, event: str, **kwargs: Any) -> None:
        """Emit a log event at the given level."""
        if HAS_STRUCTLOG and hasattr(self._logger, level):
            getattr(self._logger, level)(event, **kwargs)
        elif hasattr(self._logger, level):
            # stdlib fallback
            extra_str = " | ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
            msg = f"{event} | {extra_str}" if extra_str else event
            getattr(self._logger, level)(msg)
