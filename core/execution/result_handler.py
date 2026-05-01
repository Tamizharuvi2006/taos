"""
TAOS Result Handler — Processes and normalizes step execution results.

Handles:
- Result truncation for large outputs
- Sensitive data filtering
- Result formatting for state storage
- Error classification refinement
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from taos.core.state.state_schema import GlobalState, PlanStep, StepResult


class ResultHandler:
    """
    Production result processor.
    
    Post-processes step results before they're stored in state.
    Handles truncation, sanitization, and formatting.
    """

    MAX_RESULT_SIZE = 50_000  # 50KB max stored result

    def process(
        self,
        result: StepResult,
        step: PlanStep,
        state: GlobalState,
    ) -> StepResult:
        """
        Process a raw step result for state storage.
        
        - Truncates oversized results
        - Sanitizes error messages
        - Normalizes output format
        """
        # ─── Truncate large results ───
        if result.result is not None:
            result_str = str(result.result)
            if len(result_str) > self.MAX_RESULT_SIZE:
                result = result.model_copy(update={
                    "result": result_str[:self.MAX_RESULT_SIZE] + f"\n... [truncated, {len(result_str)} chars total]"
                })

        # ─── Sanitize error messages ───
        if result.error:
            result = result.model_copy(update={
                "error": self._sanitize_error(result.error)
            })

        return result

    def extract_delta_context(self, result: StepResult, step: PlanStep) -> str:
        """Extract a context string from a step result for state context."""
        if result.success:
            result_preview = str(result.result)[:200] if result.result else "completed"
            return f"Step '{step.id}' ({step.action}): {result_preview}"
        else:
            return f"Step '{step.id}' ({step.action}) FAILED: {result.error}"

    def aggregate_results(self, results: list[StepResult]) -> Dict[str, Any]:
        """Aggregate multiple step results into a summary."""
        total = len(results)
        succeeded = sum(1 for r in results if r.success)
        failed = total - succeeded
        total_cost = sum(r.cost for r in results)
        total_latency = sum(r.latency for r in results)

        # Get last successful result as "final output"
        last_success = None
        for r in reversed(results):
            if r.success and r.result:
                last_success = r.result
                break

        return {
            "total_steps": total,
            "succeeded": succeeded,
            "failed": failed,
            "total_cost": total_cost,
            "total_latency": total_latency,
            "final_output": last_success,
            "all_results": [
                {
                    "step_id": r.step_id,
                    "success": r.success,
                    "tool": r.tool_name,
                    "result_preview": str(r.result)[:300] if r.result else None,
                    "error": r.error,
                }
                for r in results
            ],
        }

    def _sanitize_error(self, error: str) -> str:
        """Remove sensitive data from error messages."""
        # Strip API keys if accidentally included
        import re
        error = re.sub(r'(sk-|key-|Bearer\s+)[a-zA-Z0-9_-]{20,}', r'\1[REDACTED]', error)
        # Truncate very long errors
        if len(error) > 2000:
            error = error[:2000] + "... [truncated]"
        return error
