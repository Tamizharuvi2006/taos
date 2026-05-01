from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from taos.core.reliability.provider_policy import ProviderPolicy, get_provider_policy


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class ProviderCircuitSnapshot:
    name: str
    state: str
    failures: int
    last_error: Optional[str]
    fallback_used: bool
    cache_used: bool
    circuit_opened: bool


class ProviderCircuit:
    def __init__(self, policy: ProviderPolicy) -> None:
        self.policy = policy
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_error: Optional[str] = None
        self.opened_at: Optional[float] = None
        self.fallback_used = False
        self.cache_used = False
        self.circuit_opened = False

    def allow_request(self, *, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else float(now)
        if self.state != CircuitState.OPEN:
            return True
        if self.opened_at is None:
            return False
        if now - self.opened_at >= float(self.policy.cooldown_seconds):
            self.state = CircuitState.HALF_OPEN
            return True
        return False

    def record_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_error = None
        self.opened_at = None

    def record_failure(self, error: Any, *, now: Optional[float] = None, count_failure: bool = True) -> None:
        if not count_failure:
            self.last_error = self._error_class(error)
            return
        now = time.time() if now is None else float(now)
        self.failures += 1
        self.last_error = self._error_class(error)
        if self.state == CircuitState.HALF_OPEN or self.failures >= int(self.policy.failure_threshold):
            self.state = CircuitState.OPEN
            self.opened_at = now
            self.circuit_opened = True

    def mark_fallback(self, *, cache_used: bool = False) -> None:
        self.fallback_used = True
        if cache_used:
            self.cache_used = True

    def snapshot(self) -> Dict[str, Any]:
        row = ProviderCircuitSnapshot(
            name=self.policy.name,
            state=self.state.value,
            failures=int(self.failures),
            last_error=self.last_error,
            fallback_used=bool(self.fallback_used),
            cache_used=bool(self.cache_used),
            circuit_opened=bool(self.circuit_opened),
        )
        return row.__dict__.copy()

    def reset(self) -> None:
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_error = None
        self.opened_at = None
        self.fallback_used = False
        self.cache_used = False
        self.circuit_opened = False

    def _error_class(self, error: Any) -> str:
        if error is None:
            return "unknown"
        if isinstance(error, str):
            text = error.strip()
            return text[:80] if text else "unknown"
        name = type(error).__name__
        status = getattr(error, "status_code", None)
        if status:
            return f"{name}_{status}"
        return name


def should_count_provider_failure(status_code: int | None = None, error: Any = None) -> bool:
    if status_code is not None:
        code = int(status_code)
        if code == 429 or code >= 500:
            return True
        if 400 <= code < 500:
            return False
    if error is None:
        return False
    error_name = type(error).__name__.lower()
    if "timeout" in error_name:
        return True
    return True


def create_provider_circuit(name: str) -> ProviderCircuit:
    return ProviderCircuit(get_provider_policy(name))
