from __future__ import annotations

from typing import Dict, List

from .query_plan_models import QueryPlan


class SourceLaneSearcher:
    def lane_queries(self, plan: QueryPlan) -> Dict[str, List[str]]:
        return {lane.name: list(lane.queries) for lane in plan.lanes if lane.queries}
