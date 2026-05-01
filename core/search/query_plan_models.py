from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


LANES = ("official", "news", "contradiction", "background", "technical", "regional", "fallback")


@dataclass(frozen=True)
class QueryLane:
    name: str
    queries: tuple[str, ...] = ()
    required: bool = False
    reason: str = ""


@dataclass(frozen=True)
class QueryPlan:
    original_query: str
    normalized_question: str
    intent: str
    lanes: tuple[QueryLane, ...] = ()
    raw_query_priority: str = "normal"
    metadata: Dict[str, object] = field(default_factory=dict)

    def lane(self, name: str) -> QueryLane:
        for lane in self.lanes:
            if lane.name == name:
                return lane
        return QueryLane(name=name)

    def flatten(self, *, include_fallback: bool = True) -> List[str]:
        out: List[str] = []
        seen = set()
        for lane in self.lanes:
            if lane.name == "fallback" and not include_fallback:
                continue
            for query in lane.queries:
                key = query.lower().strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(query)
        return out

    def summary(self) -> Dict[str, object]:
        return {
            "original_query": self.original_query,
            "intent": self.intent,
            "normalized_question": self.normalized_question,
            "raw_query_priority": self.raw_query_priority,
            "lanes": {lane.name: list(lane.queries) for lane in self.lanes},
            "required_lanes": [lane.name for lane in self.lanes if lane.required],
            "metadata": dict(self.metadata),
        }
