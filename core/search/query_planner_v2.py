from __future__ import annotations

from typing import Dict, List

from taos.core.understanding import IntentFrame, SearchIntentPlanner

from .query_plan_models import LANES, QueryLane, QueryPlan
from .source_lane_router import SourceLaneRouter


class SearchQueryPlannerV2:
    """Turns Phase 124A intent frames into lane-aware search plans."""

    def __init__(self) -> None:
        self._intent = SearchIntentPlanner()
        self._router = SourceLaneRouter()

    def plan(self, query: str) -> QueryPlan:
        frame = self._intent.plan(query)
        return self.plan_from_frame(frame)

    def plan_from_frame(self, frame: IntentFrame) -> QueryPlan:
        raw_lanes = frame.search_plan.as_dict()
        lane_names = self._router.lanes_for(
            intent=frame.intent,
            relation=frame.relation,
            has_country=bool(frame.entities.get("country")),
        )
        lanes: List[QueryLane] = []
        for name in LANES:
            if name not in lane_names and not raw_lanes.get(name):
                continue
            lanes.append(
                self._router.build_lane(
                    name=name,
                    queries=raw_lanes.get(name) or (),
                    intent=frame.intent,
                    relation=frame.relation,
                    reason=_lane_reason(name=name, intent=frame.intent, relation=frame.relation),
                )
            )
        return QueryPlan(
            original_query=frame.original_query,
            normalized_question=frame.normalized_question or frame.cleaned_query,
            intent=frame.intent,
            lanes=tuple(lanes),
            raw_query_priority=frame.raw_query_priority,
            metadata={
                "entities": dict(frame.entities),
                "relation": frame.relation,
                "confidence": frame.confidence,
                "needs_llm_rewrite": frame.needs_llm_rewrite,
            },
        )

    def trace_summary(self, query: str) -> Dict[str, object]:
        return self.plan(query).summary()


def _lane_reason(*, name: str, intent: str, relation: str) -> str:
    if name == "official":
        return "source-of-record verification"
    if name == "contradiction":
        return "test evidence against the claim"
    if name == "background":
        return "explain related context or possible confusion"
    if name == "technical":
        return "docs, changelog, registry, or technical source"
    if name == "regional":
        return "country or region specific context"
    if name == "fallback":
        return "raw user query retained as low-priority fallback"
    return "latest/current reporting"


def plan_query_v2(query: str) -> QueryPlan:
    return SearchQueryPlannerV2().plan(query)


def flatten_query_plan(plan: QueryPlan, *, include_fallback: bool = True) -> List[str]:
    return plan.flatten(include_fallback=include_fallback)
