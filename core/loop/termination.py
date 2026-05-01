"""
TAOS Termination Logic — Determines when and how to terminate execution.

Termination conditions:
- All steps completed successfully
- Confidence below threshold
- Cost budget exceeded
- Time limit exceeded
- Loop detected
- Unrecoverable error
- Max steps reached
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from taos.config.settings import get_settings
from taos.core.state.state_schema import GlobalState
from taos.core.execution.result_handler import ResultHandler


@dataclass
class TerminationDecision:
    """Encapsulates termination decision."""

    should_terminate: bool
    reason: str
    final_status: str  # "success" or "failed"
    graceful: bool = True  # True if clean shutdown, False if forced


class TerminationChecker:
    """
    Production termination checker.
    
    Evaluates multiple termination conditions and returns
    a decision with reason and suggested final status.
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    def check(self, state: GlobalState) -> Optional[TerminationDecision]:
        """
        Check all termination conditions.
        Returns TerminationDecision if should terminate, None otherwise.
        """
        # ─── Success: all steps completed ───
        if state.plan and state.step >= len(state.plan.steps):
            if state.status != "failed":
                return TerminationDecision(
                    should_terminate=True,
                    reason="All plan steps completed",
                    final_status="success",
                    graceful=True,
                )

        # ─── Confidence too low ───
        if state.confidence < self._settings.confidence_terminate_threshold:
            return TerminationDecision(
                should_terminate=True,
                reason=f"Confidence below threshold: {state.confidence:.2f} < {self._settings.confidence_terminate_threshold}",
                final_status="failed",
                graceful=True,
            )

        # ─── Cost budget exceeded ───
        if state.cost >= self._settings.cost_budget_per_task:
            return TerminationDecision(
                should_terminate=True,
                reason=f"Cost budget exceeded: ${state.cost:.4f} >= ${self._settings.cost_budget_per_task:.4f}",
                final_status="failed",
                graceful=True,
            )

        # ─── Time limit exceeded ───
        elapsed = time.time() - state.created_at
        if elapsed >= self._settings.max_task_time:
            return TerminationDecision(
                should_terminate=True,
                reason=f"Time limit exceeded: {elapsed:.1f}s >= {self._settings.max_task_time}s",
                final_status="timeout",
                graceful=False,
            )

        # ─── Max steps reached ───
        if state.step >= self._settings.max_steps:
            return TerminationDecision(
                should_terminate=True,
                reason=f"Max steps reached: {state.step}/{self._settings.max_steps}",
                final_status="failed",
                graceful=True,
            )

        # ─── Max replans exhausted ───
        if state.replan_count >= self._settings.max_replans:
            # Only terminate if also failing
            if state.confidence < self._settings.confidence_retry_threshold:
                return TerminationDecision(
                    should_terminate=True,
                    reason=f"Max replans exhausted ({state.replan_count}) with low confidence ({state.confidence:.2f})",
                    final_status="failed",
                    graceful=True,
                )

        return None


def build_final_result(state: GlobalState) -> dict:
    """Build the final result payload for API response."""
    result_handler = ResultHandler()
    aggregated = result_handler.aggregate_results(list(state.step_results))

    return {
        "request_id": state.request_id,
        "goal": state.goal,
        "success": state.status == "success",
        "status": state.status,
        "result": aggregated.get("final_output"),
        "steps_executed": state.step,
        "total_cost": state.cost,
        "cost_breakdown": state.cost_breakdown.model_dump(),
        "confidence": state.confidence,
        "error": state.error,
        "step_results": aggregated.get("all_results", []),
        "elapsed_time": time.time() - state.created_at,
        "state_version": state.state_version,
        "replan_count": state.replan_count,
    }
