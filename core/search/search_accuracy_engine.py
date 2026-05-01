from __future__ import annotations

from typing import Any, Dict, Iterable

from taos.core.search.query_planner_v2 import SearchQueryPlannerV2
from taos.core.understanding import UniversalUnderstandingGateway

from .result_prefilter import ResultPrefilter
from .search_plan_executor import SearchPlanExecutor
from .source_lane_searcher import SourceLaneSearcher
from .targeted_retry_planner import TargetedRetryPlanner


class SearchAccuracyEngine:
    def __init__(self) -> None:
        self._understanding = UniversalUnderstandingGateway()
        self._planner = SearchQueryPlannerV2()
        self._executor = SearchPlanExecutor()
        self._prefilter = ResultPrefilter()
        self._lane_searcher = SourceLaneSearcher()
        self._retry = TargetedRetryPlanner()

    def build_plan(self, query: str) -> Dict[str, Any]:
        frame = self._understanding.understand(query)
        plan = self._planner.plan_from_frame(frame)
        executor_summary = self._executor.summary(plan)
        return {
            "frame": frame,
            "plan": plan,
            "summary": {
                **plan.summary(),
                **executor_summary,
                "meaning_frame": frame.meaning_frame.as_dict() if frame.meaning_frame else {},
            },
            "lane_queries": self._lane_searcher.lane_queries(plan),
        }

    def prefilter_results(self, *, rows: Iterable[Dict[str, Any]], query: str) -> Dict[str, Any]:
        frame = self._understanding.understand(query)
        meaning = frame.meaning_frame
        return self._prefilter.filter(
            rows=rows,
            primary_subject=(meaning.primary_subject if meaning else ""),
            disallowed_subject_drifts=(meaning.disallowed_subject_drifts if meaning else ()),
        )

    def targeted_retry(self, *, query: str, quality_summary: Dict[str, object], query_plan_summary: Dict[str, object]) -> Dict[str, object]:
        frame = self._understanding.understand(query)
        return self._retry.plan(
            meaning_frame=frame.meaning_frame.as_dict() if frame.meaning_frame else {},
            quality_summary=quality_summary,
            query_plan_summary=query_plan_summary,
        )
