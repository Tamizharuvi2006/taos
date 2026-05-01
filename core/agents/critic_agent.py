"""CriticAgent validates step outputs for quality and consistency."""

from __future__ import annotations

from typing import Any, Dict, List

from taos.config.constants import ErrorType
from taos.core.evaluation.self_evaluator import SelfEvaluator
from taos.core.semantic.intent_classifier import IntentType
from taos.core.state.state_schema import GlobalState, PlanStep, StepResult


class CriticAgent:
    """
    Lightweight validation agent.

    It evaluates step output quality and can flag severe low-confidence outputs
    as validation failures, enabling safer reflection/replanning behavior.
    """

    def __init__(self, evaluator: SelfEvaluator | None = None) -> None:
        self._evaluator = evaluator or SelfEvaluator()

    def pre_check(self, step: PlanStep, state: GlobalState) -> Dict[str, Any]:
        """
        Validate a step before execution.

        Returns:
            {"allowed": bool, "issues": [..], "confidence": float}
        """
        _ = state
        issues: List[str] = []

        if not (step.action or "").strip():
            issues.append("Step action is empty")
        if step.tool in {"web_search", "http_request", "code_executor"} and step.tool_input is None:
            issues.append("Tool step missing tool_input")
        if step.retry_policy.max_retries < 0:
            issues.append("Retry policy has negative max_retries")

        allowed = len(issues) == 0
        return {
            "allowed": allowed,
            "issues": issues,
            "confidence": 1.0 if allowed else 0.0,
        }

    def critique(
        self,
        step: PlanStep,
        step_result: StepResult,
        state: GlobalState,
    ) -> Dict[str, Any]:
        """Return structured critique: valid, issues, confidence."""
        issues: List[str] = []

        if not step_result.success:
            if step_result.error:
                issues.append(step_result.error)
            return {
                "valid": False,
                "issues": issues or ["Step execution failed"],
                "confidence": 0.0,
            }

        output_text = str(step_result.result) if step_result.result is not None else ""
        evaluation = self._evaluator.evaluate(
            output=output_text,
            goal=state.goal or step.action,
            intent=IntentType.TASK,
        )
        issues.extend(evaluation.issues)

        return {
            "valid": evaluation.passed,
            "issues": issues,
            "confidence": round(evaluation.overall_score, 2),
        }

    def post_check(
        self,
        step: PlanStep,
        step_result: StepResult,
        state: GlobalState,
    ) -> Dict[str, Any]:
        """Post-execution critique wrapper."""
        return self.critique(step=step, step_result=step_result, state=state)

    def enforce(
        self,
        step_result: StepResult,
        critique: Dict[str, Any],
        fail_threshold: float = 0.35,
    ) -> StepResult:
        """
        Convert severe critique failures into a step failure.

        This is intentionally conservative to avoid false-negative breaks.
        """
        if step_result.success and not critique.get("valid", True):
            confidence = float(critique.get("confidence", 0.0) or 0.0)
            if confidence < fail_threshold:
                reason = ", ".join(critique.get("issues", [])[:2]) or "low-confidence output"
                return step_result.model_copy(
                    update={
                        "success": False,
                        "error": f"Critic validation failed: {reason}",
                        "error_type": ErrorType.VALIDATION_ERROR.value,
                    }
                )
        return step_result
