"""Simple circuit breaker for service client resilience."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class CircuitState:
    failures: int = 0
    opened_at: float = 0.0
    half_open_probe_in_flight: bool = False


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, cooldown_seconds: int = 60) -> None:
        self._threshold = max(1, failure_threshold)
        self._cooldown = max(1, cooldown_seconds)
        self._states: dict[str, CircuitState] = {}

    def allow(self, service_key: str) -> bool:
        st = self._states.setdefault(service_key, CircuitState())
        if st.opened_at <= 0:
            return True
        if (time.time() - st.opened_at) >= self._cooldown:
            if not st.half_open_probe_in_flight:
                st.half_open_probe_in_flight = True
                return True
        return False

    def on_success(self, service_key: str) -> None:
        st = self._states.setdefault(service_key, CircuitState())
        st.failures = 0
        st.opened_at = 0.0
        st.half_open_probe_in_flight = False

    def on_failure(self, service_key: str) -> None:
        st = self._states.setdefault(service_key, CircuitState())
        st.failures += 1
        st.half_open_probe_in_flight = False
        if st.failures >= self._threshold:
            st.opened_at = time.time()

    def is_open(self, service_key: str) -> bool:
        st = self._states.setdefault(service_key, CircuitState())
        return st.opened_at > 0 and (time.time() - st.opened_at) < self._cooldown
