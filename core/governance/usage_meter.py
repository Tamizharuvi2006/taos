from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping


LLM_CALL_COST_USD = 0.004
SEARCH_CALL_COST_USD = 0.002
EXTRACT_CALL_COST_USD = 0.001
PACKAGE_REGISTRY_CALL_COST_USD = 0.0001


@dataclass
class UsageSnapshot:
    route: str
    route_owner: str = ""
    llm_calls: int = 0
    llm_tokens: int = 0
    search_calls: int = 0
    extract_calls: int = 0
    package_registry_calls: int = 0
    cache_hits: int = 0
    fallback_count: int = 0
    estimated_cost_usd: float = 0.0
    budget_exceeded: bool = False
    latency_ms: float = 0.0
    web_search_allowed: bool = False
    research_allowed: bool = False
    doc_pipeline_allowed: bool = False
    fsm_allowed: bool = False
    entity_pipeline_called: bool = False
    doc_pipeline_called: bool = False
    fsm_called: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route": self.route,
            "route_owner": self.route_owner,
            "llm_calls": self.llm_calls,
            "llm_tokens": self.llm_tokens,
            "search_calls": self.search_calls,
            "extract_calls": self.extract_calls,
            "package_registry_calls": self.package_registry_calls,
            "cache_hits": self.cache_hits,
            "fallback_count": self.fallback_count,
            "estimated_cost_usd": round(float(self.estimated_cost_usd or 0.0), 6),
            "budget_exceeded": bool(self.budget_exceeded),
            "latency_ms": round(float(self.latency_ms or 0.0), 3),
            "web_search_allowed": bool(self.web_search_allowed),
            "research_allowed": bool(self.research_allowed),
            "doc_pipeline_allowed": bool(self.doc_pipeline_allowed),
            "fsm_allowed": bool(self.fsm_allowed),
            "entity_pipeline_called": bool(self.entity_pipeline_called),
            "doc_pipeline_called": bool(self.doc_pipeline_called),
            "fsm_called": bool(self.fsm_called),
        }


class UsageMeter:
    def __init__(self, route: str = "task") -> None:
        self._snapshot = UsageSnapshot(route=str(route or "task"))

    @classmethod
    def from_trace(cls, route: str, trace: Mapping[str, Any] | None) -> "UsageMeter":
        trace = dict(trace or {})
        timing = dict(trace.get("timing") or {})
        evidence = dict(trace.get("evidence_stats") or {})
        provider = dict(trace.get("provider_health") or {})
        route_boundary = dict(trace.get("route_boundary_summary") or {})
        route_decision = dict(trace.get("route_decision") or {})
        meter = cls(route=route)
        meter._snapshot.route_owner = str(
            route_boundary.get("owner")
            or route_decision.get("route_owner")
            or trace.get("route_owner")
            or ""
        ).strip()
        meter._snapshot.latency_ms = float(
            timing.get("total_ms")
            or timing.get("time_to_final_ms")
            or 0.0
        )
        meter._snapshot.web_search_allowed = bool(route_boundary.get("web_search_allowed"))
        meter._snapshot.research_allowed = bool(route_boundary.get("research_allowed"))
        meter._snapshot.doc_pipeline_allowed = bool(route_boundary.get("doc_pipeline_allowed"))
        meter._snapshot.fsm_allowed = bool(route_boundary.get("fsm_allowed"))
        meter.add_llm_call(tokens=int(timing.get("llm_tokens") or 0), count=int(timing.get("llm_calls") or 0))
        meter.add_search_call(count=int(evidence.get("search_calls") or evidence.get("query_count") or evidence.get("queries_run") or 0))
        meter.add_extract_call(count=int(evidence.get("extract_count") or evidence.get("extract_success_count") or 0))
        if str(evidence.get("source_type") or "").lower() == "package_registry" or evidence.get("package_registry_used"):
            meter.add_package_registry_call()
        for state in provider.values():
            if not isinstance(state, Mapping):
                continue
            if state.get("cache_used"):
                meter.add_cache_hit()
            if state.get("fallback_used"):
                meter.add_fallback()
        if trace.get("fallback_used"):
            meter.add_fallback()
        planner_path = str(trace.get("planner_path") or "").strip().lower()
        query_kind = str(trace.get("query_kind") or "").strip().lower()
        route_owner = str(meter._snapshot.route_owner or "").strip().lower()
        meter._snapshot.entity_pipeline_called = bool(
            route == "entity_lookup"
            or planner_path == "entity_lookup"
            or query_kind == "entity_lookup"
            or route_owner == "entity_lookup_pipeline"
        )
        meter._snapshot.doc_pipeline_called = bool(
            route == "doc_mode"
            or route_owner == "document_pipeline"
            or trace.get("document_summary")
            or meter._snapshot.doc_pipeline_allowed
        )
        meter._snapshot.fsm_called = bool(
            planner_path == "fsm"
            or route_owner in {"fsm_executor", "task_executor"}
            or meter._snapshot.fsm_allowed
        )
        return meter

    def add_llm_call(self, *, tokens: int = 0, count: int = 1) -> None:
        calls = max(0, int(count or 0))
        self._snapshot.llm_calls += calls
        self._snapshot.llm_tokens += max(0, int(tokens or 0))
        self._snapshot.estimated_cost_usd += calls * LLM_CALL_COST_USD

    def add_search_call(self, *, count: int = 1) -> None:
        calls = max(0, int(count or 0))
        self._snapshot.search_calls += calls
        self._snapshot.estimated_cost_usd += calls * SEARCH_CALL_COST_USD

    def add_extract_call(self, *, count: int = 1) -> None:
        calls = max(0, int(count or 0))
        self._snapshot.extract_calls += calls
        self._snapshot.estimated_cost_usd += calls * EXTRACT_CALL_COST_USD

    def add_package_registry_call(self, *, count: int = 1) -> None:
        calls = max(0, int(count or 0))
        self._snapshot.package_registry_calls += calls
        self._snapshot.estimated_cost_usd += calls * PACKAGE_REGISTRY_CALL_COST_USD

    def add_cache_hit(self, *, count: int = 1) -> None:
        self._snapshot.cache_hits += max(0, int(count or 0))

    def add_fallback(self, *, count: int = 1) -> None:
        self._snapshot.fallback_count += max(0, int(count or 0))

    def mark_budget_exceeded(self) -> None:
        self._snapshot.budget_exceeded = True

    def snapshot(self) -> UsageSnapshot:
        return UsageSnapshot(**self._snapshot.__dict__)

    def to_dict(self) -> Dict[str, Any]:
        return self.snapshot().to_dict()
