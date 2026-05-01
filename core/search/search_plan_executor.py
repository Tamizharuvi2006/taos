from __future__ import annotations

from typing import Dict, List

from .query_plan_models import QueryPlan


class SearchPlanExecutor:
    def primary_queries(self, plan: QueryPlan, *, limit: int = 12) -> List[str]:
        queries: List[str] = []
        for lane in plan.lanes:
            if lane.name == "fallback":
                continue
            queries.extend(lane.queries)
        return _dedupe(queries)[:limit]

    def fallback_queries(self, plan: QueryPlan, *, limit: int = 3) -> List[str]:
        return _dedupe(plan.lane("fallback").queries)[:limit]

    def lanes_used(self, plan: QueryPlan) -> List[str]:
        return [lane.name for lane in plan.lanes if lane.queries]

    def summary(self, plan: QueryPlan) -> Dict[str, object]:
        return {
            "lanes_used": self.lanes_used(plan),
            "primary_queries": self.primary_queries(plan),
            "fallback_queries": self.fallback_queries(plan),
            "raw_query_priority": plan.raw_query_priority,
        }


def _dedupe(values) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out
