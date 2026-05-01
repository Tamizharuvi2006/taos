from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class RouteCostBudget:
    route: str
    owner: str
    uses_tools: bool
    calls_llm: bool
    calls_research_pipeline: bool
    web_search_allowed: bool
    research_allowed: bool
    doc_pipeline_allowed: bool
    fsm_allowed: bool
    max_llm_calls: int
    max_search_calls: int
    max_extract_calls: int
    max_package_registry_calls: int
    max_estimated_cost_usd: float
    max_latency_p95_ms: int | None = None


class CostPolicy:
    def __init__(self, budgets: Dict[str, RouteCostBudget] | None = None) -> None:
        self._budgets = dict(budgets or default_route_budgets())

    def budget_for(self, route: str) -> RouteCostBudget:
        key = str(route or "task").strip().lower()
        if key == "deep_research":
            key = "deep_search"
        return self._budgets.get(key) or self._budgets["task"]

    def as_dict(self) -> Dict[str, Dict[str, float | int | str]]:
        return {route: budget.__dict__.copy() for route, budget in sorted(self._budgets.items())}


def default_route_budgets() -> Dict[str, RouteCostBudget]:
    def budget(
        route: str,
        owner: str,
        *,
        uses_tools: bool,
        calls_llm: bool,
        calls_research_pipeline: bool,
        web_search_allowed: bool,
        research_allowed: bool,
        doc_pipeline_allowed: bool,
        fsm_allowed: bool,
        llm: int,
        search: int,
        extract: int,
        package: int,
        cost: float,
        p95_ms: int | None,
    ) -> RouteCostBudget:
        return RouteCostBudget(
            route=route,
            owner=owner,
            uses_tools=uses_tools,
            calls_llm=calls_llm,
            calls_research_pipeline=calls_research_pipeline,
            web_search_allowed=web_search_allowed,
            research_allowed=research_allowed,
            doc_pipeline_allowed=doc_pipeline_allowed,
            fsm_allowed=fsm_allowed,
            max_llm_calls=llm,
            max_search_calls=search,
            max_extract_calls=extract,
            max_package_registry_calls=package,
            max_estimated_cost_usd=cost,
            max_latency_p95_ms=p95_ms,
        )

    return {
        "fast_message": budget(
            "fast_message",
            "direct",
            uses_tools=False,
            calls_llm=True,
            calls_research_pipeline=False,
            web_search_allowed=False,
            research_allowed=False,
            doc_pipeline_allowed=False,
            fsm_allowed=False,
            llm=1,
            search=0,
            extract=0,
            package=0,
            cost=0.004,
            p95_ms=1000,
        ),
        "no_search": budget(
            "no_search",
            "direct_llm_no_tools",
            uses_tools=False,
            calls_llm=True,
            calls_research_pipeline=False,
            web_search_allowed=False,
            research_allowed=False,
            doc_pipeline_allowed=False,
            fsm_allowed=False,
            llm=1,
            search=0,
            extract=0,
            package=0,
            cost=0.006,
            p95_ms=3000,
        ),
        "fast_search": budget(
            "fast_search",
            "search_lite",
            uses_tools=True,
            calls_llm=True,
            calls_research_pipeline=False,
            web_search_allowed=True,
            research_allowed=False,
            doc_pipeline_allowed=False,
            fsm_allowed=False,
            llm=1,
            search=2,
            extract=1,
            package=1,
            cost=0.01,
            p95_ms=1500,
        ),
        "package_source_of_record": budget(
            "package_source_of_record",
            "search_lite",
            uses_tools=True,
            calls_llm=False,
            calls_research_pipeline=False,
            web_search_allowed=False,
            research_allowed=False,
            doc_pipeline_allowed=False,
            fsm_allowed=False,
            llm=0,
            search=0,
            extract=0,
            package=1,
            cost=0.001,
            p95_ms=1200,
        ),
        "entity_lookup": budget(
            "entity_lookup",
            "entity_lookup_pipeline",
            uses_tools=True,
            calls_llm=True,
            calls_research_pipeline=False,
            web_search_allowed=True,
            research_allowed=True,
            doc_pipeline_allowed=False,
            fsm_allowed=False,
            llm=1,
            search=3,
            extract=3,
            package=0,
            cost=0.018,
            p95_ms=6000,
        ),
        "deep_search": budget(
            "deep_search",
            "research_pipeline",
            uses_tools=True,
            calls_llm=True,
            calls_research_pipeline=True,
            web_search_allowed=True,
            research_allowed=True,
            doc_pipeline_allowed=False,
            fsm_allowed=False,
            llm=2,
            search=5,
            extract=6,
            package=0,
            cost=0.04,
            p95_ms=16000,
        ),
        "news_search": budget(
            "news_search",
            "research_pipeline",
            uses_tools=True,
            calls_llm=True,
            calls_research_pipeline=True,
            web_search_allowed=True,
            research_allowed=True,
            doc_pipeline_allowed=False,
            fsm_allowed=False,
            llm=2,
            search=5,
            extract=6,
            package=0,
            cost=0.045,
            p95_ms=12000,
        ),
        "official_search": budget(
            "official_search",
            "research_pipeline",
            uses_tools=True,
            calls_llm=True,
            calls_research_pipeline=True,
            web_search_allowed=True,
            research_allowed=True,
            doc_pipeline_allowed=False,
            fsm_allowed=False,
            llm=2,
            search=4,
            extract=5,
            package=0,
            cost=0.035,
            p95_ms=18000,
        ),
        "comparison_search": budget(
            "comparison_search",
            "research_pipeline",
            uses_tools=True,
            calls_llm=True,
            calls_research_pipeline=True,
            web_search_allowed=True,
            research_allowed=True,
            doc_pipeline_allowed=False,
            fsm_allowed=False,
            llm=2,
            search=6,
            extract=6,
            package=0,
            cost=0.05,
            p95_ms=15000,
        ),
        "doc_mode": budget(
            "doc_mode",
            "document_pipeline",
            uses_tools=True,
            calls_llm=True,
            calls_research_pipeline=False,
            web_search_allowed=False,
            research_allowed=False,
            doc_pipeline_allowed=True,
            fsm_allowed=False,
            llm=1,
            search=0,
            extract=0,
            package=0,
            cost=0.015,
            p95_ms=5000,
        ),
        "clarification": budget(
            "clarification",
            "clarification_fallback",
            uses_tools=False,
            calls_llm=True,
            calls_research_pipeline=False,
            web_search_allowed=False,
            research_allowed=False,
            doc_pipeline_allowed=False,
            fsm_allowed=False,
            llm=1,
            search=0,
            extract=0,
            package=0,
            cost=0.003,
            p95_ms=1200,
        ),
        "task": budget(
            "task",
            "fsm_executor",
            uses_tools=True,
            calls_llm=True,
            calls_research_pipeline=False,
            web_search_allowed=False,
            research_allowed=False,
            doc_pipeline_allowed=False,
            fsm_allowed=True,
            llm=3,
            search=3,
            extract=3,
            package=0,
            cost=0.06,
            p95_ms=10000,
        ),
    }


def default_cost_policy() -> CostPolicy:
    return CostPolicy()
