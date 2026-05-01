"""
TAOS Backoff Strategies — Delay computation for retries.

Implements:
- Exponential backoff (default)
- Linear backoff
- Fixed delay
- Jittered backoff (production recommended)
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod


class BackoffStrategy(ABC):
    """Base class for backoff strategies."""

    @abstractmethod
    def compute(self, attempt: int) -> float:
        """Compute delay in seconds for the given attempt number."""
        ...

    @abstractmethod
    def reset(self) -> None:
        """Reset internal state."""
        ...


class ExponentialBackoff(BackoffStrategy):
    """
    Exponential backoff: delay = base * multiplier^attempt
    Capped at max_delay. Optional jitter for production use.
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        multiplier: float = 2.0,
        jitter: bool = True,
    ):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.jitter = jitter

    def compute(self, attempt: int) -> float:
        delay = self.base_delay * (self.multiplier ** attempt)
        delay = min(delay, self.max_delay)
        if self.jitter:
            delay = delay * (0.5 + random.random() * 0.5)  # 50-100% of computed delay
        return delay

    def reset(self) -> None:
        pass


class LinearBackoff(BackoffStrategy):
    """Linear backoff: delay = base + increment * attempt"""

    def __init__(
        self,
        base_delay: float = 1.0,
        increment: float = 1.0,
        max_delay: float = 30.0,
    ):
        self.base_delay = base_delay
        self.increment = increment
        self.max_delay = max_delay

    def compute(self, attempt: int) -> float:
        delay = self.base_delay + (self.increment * attempt)
        return min(delay, self.max_delay)

    def reset(self) -> None:
        pass


class FixedBackoff(BackoffStrategy):
    """Fixed delay between retries."""

    def __init__(self, delay: float = 2.0):
        self.delay = delay

    def compute(self, attempt: int) -> float:
        return self.delay

    def reset(self) -> None:
        pass


class DecorrelatedJitterBackoff(BackoffStrategy):
    """
    AWS-recommended decorrelated jitter backoff.
    delay = min(max_delay, random_between(base, prev_delay * 3))
    Best for production workloads with many concurrent retriers.
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
    ):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._prev_delay = base_delay

    def compute(self, attempt: int) -> float:
        delay = random.uniform(self.base_delay, self._prev_delay * 3)
        delay = min(delay, self.max_delay)
        self._prev_delay = delay
        return delay

    def reset(self) -> None:
        self._prev_delay = self.base_delay
