from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from taos.core.state.state_schema import GlobalState


@dataclass(frozen=True)
class PersistenceResult:
    attempted: bool
    success: bool
    error: Optional[str] = None


class PersistenceCoordinator:
    """Best-effort persistence boundary for non-critical execution writes."""

    async def persist_execution_memory(
        self,
        *,
        engine: Any,
        user_id: str,
        state: GlobalState,
        result: Dict[str, Any],
        latency_ms: float,
    ) -> PersistenceResult:
        try:
            await engine._persist_execution_memory_legacy(
                user_id=user_id,
                state=state,
                result=result,
                latency_ms=latency_ms,
            )
            return PersistenceResult(attempted=True, success=True)
        except Exception as exc:  # pragma: no cover - legacy path already catches, this is a belt.
            engine._log("engine.persistence_coordinator_error", error=str(exc))
            return PersistenceResult(attempted=True, success=False, error=str(exc))
