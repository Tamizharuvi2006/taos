from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Type


@dataclass
class RouteExecutionContext:
    query: str
    raw_query: str
    normalized_query: Optional[str]
    user_id: Optional[str]
    route_decision: Dict[str, Any]
    interpretation: Dict[str, Any]
    include_trace: bool
    request_budget: Any
    metadata: Dict[str, Any]
    engine: Any = None
    classification: Any = None
    tracker: Any = None
    doc_context_active: bool = False
    doc_ids: List[str] = field(default_factory=list)


@dataclass
class RouteExecutionResult:
    answer: str = ""
    route: str = ""
    owner: str = ""
    confidence: float = 0.0
    sources: List[Any] = field(default_factory=list)
    sections: Dict[str, Any] = field(default_factory=dict)
    trust: Dict[str, Any] = field(default_factory=dict)
    trace: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    payload: Optional[Dict[str, Any]] = None


class RouteHandler(Protocol):
    async def handle(self, context: RouteExecutionContext) -> RouteExecutionResult:
        ...


class RouteDispatcher:
    RESEARCH_ROUTES = {"deep_search", "deep_research", "news_search", "official_search", "comparison_search"}

    def __init__(self) -> None:
        from taos.orchestration.handlers import (
            ClarificationHandler,
            DocumentHandler,
            FastMessageHandler,
            FastSearchHandler,
            NoSearchHandler,
            ResearchHandler,
            TaskHandler,
        )

        self._route_handlers: Dict[str, Type[RouteHandler]] = {
            "fast_message": FastMessageHandler,
            "no_search": NoSearchHandler,
            "fast_search": FastSearchHandler,
            "deep_search": ResearchHandler,
            "deep_research": ResearchHandler,
            "news_search": ResearchHandler,
            "official_search": ResearchHandler,
            "comparison_search": ResearchHandler,
            "doc_mode": DocumentHandler,
            "document_pipeline": DocumentHandler,
            "task": TaskHandler,
            "standard_task": TaskHandler,
            "clarification": ClarificationHandler,
        }
        self._owner_handlers: Dict[str, Type[RouteHandler]] = {
            "direct_fast_message": FastMessageHandler,
            "direct_llm_no_tools": NoSearchHandler,
            "search_lite": FastSearchHandler,
            "research_pipeline": ResearchHandler,
            "document_pipeline": DocumentHandler,
            "fsm_planner": TaskHandler,
            "direct_standard": TaskHandler,
            "clarification_fallback": ClarificationHandler,
        }

    def handler_class_for(self, *, route: str = "", owner: str = "") -> Type[RouteHandler]:
        owner_key = str(owner or "").strip().lower()
        route_key = str(route or "").strip().lower()
        if owner_key in self._owner_handlers:
            return self._owner_handlers[owner_key]
        if route_key in self._route_handlers:
            return self._route_handlers[route_key]
        return self._owner_handlers["direct_standard"]

    def owner_for_route(self, route: str) -> str:
        route_key = str(route or "").strip().lower()
        if route_key == "fast_message":
            return "direct_fast_message"
        if route_key == "no_search":
            return "direct_llm_no_tools"
        if route_key == "fast_search":
            return "search_lite"
        if route_key in self.RESEARCH_ROUTES:
            return "research_pipeline"
        if route_key == "doc_mode":
            return "document_pipeline"
        if route_key in {"task", "standard_task"}:
            return "fsm_planner"
        if route_key == "clarification":
            return "clarification_fallback"
        return "direct_standard"

    async def dispatch(self, context: RouteExecutionContext) -> RouteExecutionResult:
        owner = str((context.route_decision or {}).get("route_owner") or context.metadata.get("route_owner") or "").strip()
        route = str((context.route_decision or {}).get("route") or context.metadata.get("route") or "").strip()
        handler = self.handler_class_for(route=route, owner=owner)()
        return await handler.handle(context)
