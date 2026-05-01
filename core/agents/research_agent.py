"""ResearchAgent for search-heavy steps with source-quality post-processing."""

from __future__ import annotations

import httpx

from taos.config.model_config import ModelOrchestration
from taos.config.settings import get_settings
from taos.core.agents.base_agent import BaseAgent
from taos.core.execution.executor import Executor
from taos.core.semantic.intent_classifier import DomainType
from taos.core.state.state_schema import GlobalState, PlanStep, StepResult
from taos.core.tools.source_ranker import SourceRanker


class ResearchAgent(BaseAgent):
    """
    Specialized agent for research-oriented steps.

    It reuses the existing executor pipeline, then enriches successful
    `web_search` outputs with ranked and filtered sources.
    """

    def __init__(self, executor: Executor, source_ranker: SourceRanker | None = None) -> None:
        self._executor = executor
        self._ranker = source_ranker or SourceRanker()
        self._settings = get_settings()
        self._model_config = ModelOrchestration().get_config("executor")

    @property
    def name(self) -> str:
        return "research_agent"

    async def execute(
        self,
        step: PlanStep,
        state: GlobalState,
        step_index: int,
    ) -> StepResult:
        base_result = await self._executor.execute_step(
            step=step,
            state=state,
            step_index=step_index,
        )

        if not base_result.success or step.tool != "web_search":
            return base_result

        raw = base_result.result
        if not isinstance(raw, dict):
            return base_result

        raw_results = raw.get("results", [])
        if not isinstance(raw_results, list) or not raw_results:
            return base_result

        filtered = self._filter_low_quality(raw_results)
        ranked = self._ranker.rank(
            results=filtered,
            domain=DomainType.GENERAL,
            max_results=min(len(filtered), 8),
        )
        ranked = self._ranker.filter_irrelevant(ranked, min_score=0.2)
        summary = await self._summarize_ranked(step, ranked)

        enriched_result = dict(raw)
        enriched_result["ranked_results"] = [
            {
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "rank_score": round(item.rank_score, 3),
                "tier": item.tier,
                "provider": item.provider,
            }
            for item in ranked
        ]
        enriched_result["research_summary"] = summary

        return base_result.model_copy(update={"result": enriched_result})

    def _filter_low_quality(self, results: list[dict]) -> list[dict]:
        """Drop empty/weak snippets before ranking."""
        filtered: list[dict] = []
        for result in results:
            title = str(result.get("title", "")).strip()
            snippet = str(result.get("snippet", "")).strip()
            link = str(result.get("link", result.get("url", ""))).strip()
            if not link:
                continue
            if len(title) < 3 and len(snippet) < 20:
                continue
            filtered.append(result)
        return filtered or results

    async def _summarize_ranked(self, step: PlanStep, ranked: list) -> str:
        """Summarize ranked sources into a concise synthesis."""
        if not ranked:
            return "No high-quality sources found."

        top = ranked[:3]
        source_lines = []
        for idx, item in enumerate(top, start=1):
            source_lines.append(
                f"{idx}. {item.title} | {item.provider} | {item.snippet[:280]}"
            )
        source_block = "\n".join(source_lines)
        prompt = (
            "Summarize these ranked research sources in 3 bullet points.\n"
            "Each bullet must include one concrete fact and source name.\n"
            "Do not invent facts.\n\n"
            f"Step Action: {step.action}\n"
            f"Sources:\n{source_block}"
        )

        llm_summary = await self._llm_summarize(prompt)
        if llm_summary:
            return llm_summary

        # Heuristic fallback if LLM is unavailable.
        return "\n".join(
            f"- {item.title}: {item.snippet[:140]} ({item.provider})"
            for item in top
        )

    async def _llm_summarize(self, prompt: str) -> str | None:
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
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{self._settings.openrouter_base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return None
