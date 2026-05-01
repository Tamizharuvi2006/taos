"""
TAOS Loop Guard — Detects and prevents infinite execution loops.

Detection methods:
- Repeated state hash tracking
- Action pattern repetition
- Cost rate monitoring
- Step count hard limits
"""

from __future__ import annotations

from typing import List, Optional

from taos.config.settings import get_settings
from taos.core.state.state_schema import GlobalState


class LoopDetected(Exception):
    """Raised when an execution loop is detected."""
    pass


class LoopGuard:
    """
    Production loop detection system.
    
    Monitors execution patterns and detects when the agent
    is stuck in a loop. Multiple detection strategies combined.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._state_hashes: List[str] = []
        self._action_history: List[str] = []
        self._error_history: List[str] = []

    def record_state(self, state: GlobalState) -> None:
        """Record a state hash for loop detection."""
        self._state_hashes.append(state.compute_hash())

    def record_action(self, action: str) -> None:
        """Record an action for pattern detection."""
        self._action_history.append(action)

    def record_error(self, error: str) -> None:
        """Record an error for repeated failure detection."""
        self._error_history.append(error)

    def check_all(self, state: GlobalState) -> Optional[str]:
        """
        Run all loop detection checks.
        Returns reason string if loop detected, None otherwise.
        """
        checks = [
            self._check_repeated_state,
            self._check_repeated_actions,
            self._check_repeated_errors,
            self._check_step_limit,
        ]

        for check in checks:
            reason = check(state)
            if reason:
                return reason
        return None

    def _check_repeated_state(self, state: GlobalState) -> Optional[str]:
        """Detect repeated identical states."""
        threshold = self._settings.repeated_state_threshold
        if len(self._state_hashes) < threshold:
            return None

        recent = self._state_hashes[-threshold:]
        if len(set(recent)) == 1:
            return f"State hash repeated {threshold} times consecutively"
        return None

    def _check_repeated_actions(self, state: GlobalState) -> Optional[str]:
        """Detect repeated identical actions."""
        if len(self._action_history) < 4:
            return None

        recent = self._action_history[-4:]
        if len(set(recent)) == 1:
            return f"Same action repeated 4 times: '{recent[0][:50]}'"
        return None

    def _check_repeated_errors(self, state: GlobalState) -> Optional[str]:
        """Detect the same error occurring repeatedly."""
        if len(self._error_history) < 3:
            return None

        recent = self._error_history[-3:]
        if len(set(recent)) == 1:
            return f"Same error repeated 3 times: '{recent[0][:50]}'"
        return None

    def _check_step_limit(self, state: GlobalState) -> Optional[str]:
        """Hard step limit check."""
        if state.step >= self._settings.max_steps:
            return f"Step limit reached: {state.step}/{self._settings.max_steps}"
        return None

    def reset(self) -> None:
        """Reset all tracking data."""
        self._state_hashes.clear()
        self._action_history.clear()
        self._error_history.clear()
