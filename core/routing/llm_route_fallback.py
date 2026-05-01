from __future__ import annotations

import json
from typing import Any, Dict, Optional


class TinyLLMRouteFallback:
    """Cheap, timeout-bounded route fallback for genuinely ambiguous queries."""

    VALID_ROUTES = {
        "fast_message",
        "no_search",
        "fast_search",
        "deep_search",
        "news_search",
        "official_search",
        "comparison_search",
        "doc_mode",
        "task",
        "clarification",
    }

    def __init__(self) -> None:
        self._settings = None
        self._model_config = None
        try:
            from taos.config.model_config import ModelOrchestration
            from taos.config.settings import get_settings

            self._settings = get_settings()
            self._model_config = ModelOrchestration().get_config("executor")
        except Exception:
            # Keep deterministic routing importable in lightweight test/CI
            # environments that do not install provider dependencies.
            self._settings = None
            self._model_config = None

    async def decide(self, query: str, context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if self._settings is None or self._model_config is None:
            return None
        if not str(getattr(self._settings, "openrouter_api_key", "") or "").strip():
            return None
        context = dict(context or {})
        prompt = (
            "Route this user request into exactly one TAOS route.\n"
            "Routes: fast_message, no_search, fast_search, deep_search, news_search, "
            "official_search, comparison_search, doc_mode, task, clarification.\n"
            "Rules: definitions without freshness use no_search; current/latest uses fast_search; "
            "news/breaking/today uses news_search; documents use doc_mode; actions/code use task; "
            "high-stakes factual questions use official_search; unsafe ambiguous high-stakes use clarification.\n"
            "Return strict JSON only: "
            '{"route":"no_search","confidence":0.72,"reason":"short reason"}\n\n'
            f"Context: has_active_doc={bool(context.get('has_active_doc'))}, "
            f"has_context={bool(context.get('has_context'))}\n"
            f"Query: {query}"
        )
        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "HTTP-Referer": self._settings.site_url,
            "X-Title": self._settings.site_name,
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model_config.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            **self._model_config.to_api_params(),
        }
        try:
            import httpx

            async with httpx.AsyncClient(timeout=1.2) as client:
                resp = await client.post(
                    f"{self._settings.openrouter_base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)
        except Exception:
            return None
        route = str(parsed.get("route") or "").strip().lower()
        if route not in self.VALID_ROUTES:
            return None
        return {
            "route": route,
            "confidence": max(0.0, min(1.0, float(parsed.get("confidence") or 0.0))),
            "reason": str(parsed.get("reason") or "llm_route_fallback").strip(),
        }
