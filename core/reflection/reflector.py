"""
TAOS Reflector — LLM-powered reflection engine.

Evaluates step results to determine:
- Was the step successful?
- What's the confidence level?
- Should we retry, continue, or terminate?
- What went wrong and what can be improved?

Production features:
- Structured reflection output via LLM
- Confidence scoring with fallback heuristics
- Error classification
- Actionable suggestions
"""

from __future__ import annotations

import json

import httpx

from taos.config.constants import ErrorType
from taos.config.settings import get_settings
from taos.core.state.state_schema import (
    GlobalState,
    PlanStep,
    ReflectionResult,
    StepResult,
)


REFLECTION_SYSTEM_PROMPT = """You are TAOS Reflector — a production-grade evaluation engine.

Your job: Evaluate the result of an execution step and produce a structured assessment.

Evaluate based on:
1. Did the step achieve its intended action?
2. Is the result useful toward the overall goal?
3. Are there any errors or quality issues?
4. Should the step be retried with a different approach?

OUTPUT FORMAT (strict JSON):
{
  "success": true/false,
  "confidence": 0.0-1.0,
  "error_type": null or "TOOL_FAILURE"/"PLANNER_ERROR"/"TIMEOUT"/etc,
  "retry_recommended": true/false,
  "reasoning": "Brief explanation of assessment",
  "suggestions": ["suggestion1", "suggestion2"]
}

Confidence guide:
- 0.9-1.0: Perfect result, fully achieved the step
- 0.7-0.9: Good result with minor issues
- 0.5-0.7: Partial result, may need retry or replan
- 0.3-0.5: Poor result, retry recommended
- 0.0-0.3: Failed, terminate recommended"""

REFLECTION_USER_PROMPT = """GOAL: {goal}

STEP ACTION: {action}
STEP TOOL: {tool}

STEP RESULT:
Success: {success}
Output: {result}
Error: {error}

PREVIOUS CONTEXT:
{context}

Evaluate this step result. Respond with ONLY valid JSON."""


class Reflector:
    """
    Production reflection engine.
    
    Uses LLM to evaluate step results and produce structured
    confidence assessments with retry recommendations.
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    async def reflect(
        self,
        step_result: StepResult,
        step: PlanStep,
        state: GlobalState,
    ) -> ReflectionResult:
        """
        Reflect on a step result.
        
        Tries LLM-based reflection first, falls back to heuristic
        if LLM is unavailable or fails.
        """
        try:
            return await self._llm_reflect(step_result, step, state)
        except Exception:
            # Fallback to heuristic reflection
            return self._heuristic_reflect(step_result, step)

    async def _llm_reflect(
        self,
        step_result: StepResult,
        step: PlanStep,
        state: GlobalState,
    ) -> ReflectionResult:
        """LLM-powered reflection via OpenRouter."""
        context_str = "\n".join(state.context[-5:]) if state.context else "No prior context."
        result_str = str(step_result.result)[:3000] if step_result.result else "No output"

        user_prompt = REFLECTION_USER_PROMPT.format(
            goal=state.goal,
            action=step.action,
            tool=step.tool or "none (reasoning step)",
            success=step_result.success,
            result=result_str,
            error=step_result.error or "none",
            context=context_str,
        )

        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self._settings.reflection_model,
            "messages": [
                {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 512,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self._settings.openrouter_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]

        # Parse JSON response
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        parsed = json.loads(text)

        return ReflectionResult(
            success=parsed.get("success", step_result.success),
            confidence=max(0.0, min(1.0, parsed.get("confidence", 0.5))),
            error_type=parsed.get("error_type"),
            retry_recommended=parsed.get("retry_recommended", False),
            reasoning=parsed.get("reasoning", ""),
            suggestions=parsed.get("suggestions", []),
        )

    def _heuristic_reflect(
        self,
        step_result: StepResult,
        step: PlanStep,
    ) -> ReflectionResult:
        """
        Heuristic fallback reflection (no LLM needed).
        Used when LLM is unavailable or for cost savings.
        """
        if step_result.success:
            # Success — check result quality
            has_result = step_result.result is not None
            result_length = len(str(step_result.result)) if has_result else 0

            if result_length > 100:
                confidence = 0.85
            elif result_length > 10:
                confidence = 0.7
            else:
                confidence = 0.55

            return ReflectionResult(
                success=True,
                confidence=confidence,
                reasoning=f"Step completed successfully. Result length: {result_length} chars.",
                suggestions=[],
            )
        else:
            # Failure — classify and recommend
            error_type = step_result.error_type or ErrorType.UNKNOWN
            retry = error_type in {
                ErrorType.TOOL_FAILURE,
                ErrorType.TIMEOUT,
                ErrorType.TOOL_RATE_LIMIT,
                ErrorType.MEMORY_MISS,
            }

            if error_type == ErrorType.TIMEOUT:
                confidence = 0.4
            elif error_type in {ErrorType.TOOL_AUTH_FAILURE, ErrorType.COST_EXCEEDED}:
                confidence = 0.1
            elif error_type == ErrorType.TOOL_RATE_LIMIT:
                confidence = 0.45
            else:
                confidence = 0.3

            return ReflectionResult(
                success=False,
                confidence=confidence,
                error_type=error_type,
                retry_recommended=retry,
                reasoning=f"Step failed with {error_type}: {step_result.error}",
                suggestions=["Retry with different parameters"] if retry else ["Consider alternative approach"],
            )
