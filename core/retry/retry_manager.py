"""
TAOS Retry Manager — Orchestrates retry logic across steps.

Coordinates with:
- Controller (for state transitions)
- Backoff strategies (for timing)
- Error classification (for retry eligibility)
"""

from __future__ import annotations

from typing import List, Optional

from taos.config.constants import ErrorType, RETRYABLE_ERRORS, NON_RETRYABLE_ERRORS
from taos.config.settings import get_settings
from taos.core.retry.backoff import BackoffStrategy, ExponentialBackoff
from taos.core.state.state_schema import StepResult


class RetryDecision:
    """Encapsulates a retry decision with reasoning."""

    def __init__(
        self,
        should_retry: bool,
        reason: str,
        delay: float = 0.0,
        attempt: int = 0,
        max_attempts: int = 3,
    ):
        self.should_retry = should_retry
        self.reason = reason
        self.delay = delay
        self.attempt = attempt
        self.max_attempts = max_attempts

    def __repr__(self) -> str:
        return f"RetryDecision(retry={self.should_retry}, attempt={self.attempt}/{self.max_attempts}, reason={self.reason})"


class RetryManager:
    """
    Production retry manager.
    
    Determines whether to retry a failed step based on:
    - Error type (retryable vs non-retryable)
    - Attempt count vs max retries
    - Backoff strategy
    """

    def __init__(
        self,
        backoff: Optional[BackoffStrategy] = None,
    ) -> None:
        self._settings = get_settings()
        self._backoff = backoff or ExponentialBackoff(
            base_delay=1.0,
            max_delay=30.0,
            multiplier=2.0,
        )
        self._attempt_counts: dict[str, int] = {}  # step_id -> attempt count

    def should_retry(self, step_result: StepResult) -> RetryDecision:
        """
        Determine if a failed step should be retried.
        """
        step_id = step_result.step_id
        attempt = self._attempt_counts.get(step_id, 0)
        max_attempts = self._settings.max_retries

        # ─── Already succeeded ───
        if step_result.success:
            return RetryDecision(
                should_retry=False,
                reason="Step succeeded",
                attempt=attempt,
                max_attempts=max_attempts,
            )

        # ─── Max retries exceeded ───
        if attempt >= max_attempts:
            return RetryDecision(
                should_retry=False,
                reason=f"Max retries exceeded ({attempt}/{max_attempts})",
                attempt=attempt,
                max_attempts=max_attempts,
            )

        # ─── Non-retryable error ───
        error_type = step_result.error_type
        if error_type and error_type in {e.value for e in NON_RETRYABLE_ERRORS}:
            return RetryDecision(
                should_retry=False,
                reason=f"Non-retryable error: {error_type}",
                attempt=attempt,
                max_attempts=max_attempts,
            )

        # ─── Retryable — compute delay ───
        delay = self._backoff.compute(attempt)
        self._attempt_counts[step_id] = attempt + 1

        return RetryDecision(
            should_retry=True,
            reason=f"Retryable error: {error_type}",
            delay=delay,
            attempt=attempt + 1,
            max_attempts=max_attempts,
        )

    def get_attempt_count(self, step_id: str) -> int:
        """Get current attempt count for a step."""
        return self._attempt_counts.get(step_id, 0)

    def reset(self, step_id: Optional[str] = None) -> None:
        """Reset attempt counts."""
        if step_id:
            self._attempt_counts.pop(step_id, None)
        else:
            self._attempt_counts.clear()
