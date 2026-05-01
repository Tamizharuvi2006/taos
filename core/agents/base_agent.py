"""
Base interface for all TAOS specialized agents.

Agents never mutate global state directly. They execute work for a step and
return a StepResult. State transitions remain Controller-owned.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from taos.core.state.state_schema import GlobalState, PlanStep, StepResult


class BaseAgent(ABC):
    """Abstract specialized agent contract."""

    @abstractmethod
    async def execute(
        self,
        step: PlanStep,
        state: GlobalState,
        step_index: int,
    ) -> StepResult:
        """Execute a single plan step and return a structured step result."""
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable agent name used by router/debug logs."""
        raise NotImplementedError
