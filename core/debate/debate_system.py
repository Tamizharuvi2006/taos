"""Multi-agent debate: Pro vs Challenger with Judge merge."""

from __future__ import annotations

from typing import Optional

import httpx

from taos.config.model_config import ModelOrchestration
from taos.config.settings import get_settings


class DebateSystem:
    """Trigger-based debate refinement for high-risk/high-complexity outputs."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._model_config = ModelOrchestration().get_config("executor")

    def should_trigger(
        self,
        complexity: str,
        confidence: float,
        critic_flagged: bool = False,
    ) -> bool:
        # Strict cost-aware triggering policy.
        if complexity == "high":
            return True
        if confidence < 0.6:
            return True
        if critic_flagged:
            return True
        return False

    async def debate_and_resolve(
        self,
        goal: str,
        base_answer: str,
    ) -> str:
        """Run pro/challenger/judge sequence and return final merged answer."""
        pro_prompt = (
            "You are Agent A (Primary). Improve the base answer with stronger facts, "
            "better structure, and directness.\n\n"
            f"Goal: {goal}\n\nBase answer:\n{base_answer}"
        )
        challenger_prompt = (
            "You are Agent B (Challenger). Critique Agent A output. Find flaws, stale facts, "
            "missing context, and contradictions. Propose a better alternative answer.\n\n"
            f"Goal: {goal}\n\nBase answer:\n{base_answer}"
        )

        pro = await self._run_llm(pro_prompt)
        challenger = await self._run_llm(challenger_prompt)
        if not pro or not challenger:
            return base_answer

        judge_prompt = (
            "You are the Judge Agent. Compare Agent A and Agent B. Produce one best final answer.\n"
            "Rules: keep only valid points, resolve contradictions, avoid fluff.\n\n"
            f"Goal: {goal}\n\n"
            f"Agent A:\n{pro}\n\n"
            f"Agent B:\n{challenger}\n\n"
            "Return only the final merged answer."
        )
        judged = await self._run_llm(judge_prompt)
        return judged or pro or base_answer

    async def _run_llm(self, prompt: str) -> Optional[str]:
        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "HTTP-Referer": self._settings.site_url,
            "X-Title": self._settings.site_name,
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model_config.model_id,
            "messages": [{"role": "user", "content": prompt}],
            **self._model_config.to_api_params(),
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._settings.openrouter_base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return None
