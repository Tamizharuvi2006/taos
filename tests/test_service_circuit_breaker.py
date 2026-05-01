from __future__ import annotations

import time

from taos.core.services.circuit_breaker import CircuitBreaker


def test_circuit_breaker_opens_and_recovers():
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=1)
    key = "planner"
    assert cb.allow(key) is True
    cb.on_failure(key)
    assert cb.allow(key) is True
    cb.on_failure(key)
    assert cb.is_open(key) is True
    assert cb.allow(key) is False
    time.sleep(1.05)
    assert cb.allow(key) is True
    cb.on_success(key)
    assert cb.is_open(key) is False
