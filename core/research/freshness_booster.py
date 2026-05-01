from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from .freshness_policy import FreshnessPolicy


class FreshnessBooster:
    """Runs one extra recency-focused query only when freshness is weak."""

    def __init__(self, threshold: float = 0.72) -> None:
        self.threshold = max(0.0, min(1.0, float(threshold)))
        self._policy = FreshnessPolicy()

    def should_boost(self, *, query: str, freshness_summary: Dict[str, Any]) -> bool:
        decision = self._policy.decide(query)
        if decision.mode not in {"news_live", "current_lookup"}:
            return False
        score = float((freshness_summary or {}).get("freshness_score") or 0.0)
        return score < self.threshold

    def build_query(self, query: str) -> str:
        text = str(query or "").strip()
        if not text:
            return "latest official update today"
        return f"{text} latest official update today"

    async def boost(
        self,
        *,
        query: str,
        rows: Iterable[Dict[str, Any]],
        freshness_summary: Dict[str, Any],
        web_search_fn: Callable[..., Awaitable[Dict[str, Any]]],
        search_type: str = "search",
        recency_days: Optional[int] = 7,
    ) -> Dict[str, Any]:
        base_rows = [dict(row) for row in rows or []]
        if not self.should_boost(query=query, freshness_summary=freshness_summary):
            return {"rows": base_rows, "summary": {"boosted": False, "reason": "freshness_sufficient"}}
        boost_query = self.build_query(query)
        added: List[Dict[str, Any]] = []
        try:
            response = await web_search_fn(
                query=boost_query,
                num_results=5,
                search_type=search_type,
                recency_days=recency_days,
            )
        except Exception as exc:
            return {"rows": base_rows, "summary": {"boosted": False, "reason": type(exc).__name__, "query": boost_query}}
        for row in list((response or {}).get("results") or []):
            title = str(row.get("title") or "").strip()
            link = str(row.get("link") or "").strip()
            snippet = str(row.get("snippet") or "").strip()
            if title and link and snippet:
                added.append({"title": title, "link": link, "snippet": snippet, "query": boost_query})
        merged = self.merge_rows(base_rows, added)
        return {
            "rows": merged,
            "summary": {
                "boosted": bool(added),
                "query": boost_query,
                "added_rows": len(added),
                "before_count": len(base_rows),
                "after_count": len(merged),
            },
        }

    def merge_rows(self, rows: Iterable[Dict[str, Any]], new_rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for row in list(rows or []) + list(new_rows or []):
            link = str(row.get("link") or row.get("url") or "").strip().lower()
            key = link or str(row.get("title") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(dict(row))
        return merged
