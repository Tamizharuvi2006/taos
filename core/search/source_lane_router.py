from __future__ import annotations

from typing import Iterable, List

from .query_plan_models import QueryLane


class SourceLaneRouter:
    REQUIRED_OFFICIAL_RELATIONS = {
        "blocked_or_restricted_access",
        "unavailable_or_outage",
        "pricing_changed",
        "released_or_changed",
        "security_concern",
    }

    def lanes_for(self, *, intent: str, relation: str, has_country: bool = False) -> List[str]:
        lanes = ["news", "background"]
        if intent == "rumour_verification":
            lanes.extend(["official", "contradiction"])
        if intent == "official_verification" or relation in self.REQUIRED_OFFICIAL_RELATIONS:
            lanes.append("official")
        if relation in {"pricing_changed", "released_or_changed"}:
            lanes.append("technical")
        if has_country:
            lanes.append("regional")
        lanes.append("fallback")
        return _dedupe(lanes)

    def required(self, *, lane: str, intent: str, relation: str) -> bool:
        if lane == "official" and (intent == "official_verification" or relation in self.REQUIRED_OFFICIAL_RELATIONS):
            return True
        if lane == "contradiction" and intent == "rumour_verification":
            return True
        if lane == "background" and intent == "rumour_verification":
            return True
        return False

    def build_lane(self, *, name: str, queries: Iterable[str], intent: str, relation: str, reason: str = "") -> QueryLane:
        return QueryLane(
            name=name,
            queries=tuple(_dedupe(str(query or "").strip() for query in queries)),
            required=self.required(lane=name, intent=intent, relation=relation),
            reason=reason,
        )


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(text)
    return out
