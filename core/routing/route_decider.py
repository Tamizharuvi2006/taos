from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
import re
from typing import Any, Dict, List, Optional

from taos.core.understanding.universal_understanding_gateway import (
    UniversalUnderstandingGateway,
    frame_to_trace_summary,
)

from .global_hybrid_router import GlobalHybridRouter
from .llm_route_fallback import TinyLLMRouteFallback
from .route_cache import RouteCache, normalize_query
from .route_rules import RuleDecision, deterministic_route, safe_default_route
from .route_scorer import score_routes


VALID_ROUTES = {
    "fast_message",
    "no_search",
    "fast_search",
    "deep_search",
    "news_search",
    "official_search",
    "comparison_search",
    "entity_lookup",
    "doc_mode",
    "task",
    "clarification",
}


@dataclass
class RouteDecision:
    route: str
    confidence: float
    reason: str
    matched_rules: List[str] = field(default_factory=list)
    used_llm: bool = False
    cache_status: str = "miss"
    boundary: str = "deterministic"
    high_stakes: bool = False
    routing_signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["selected_route"] = self.route
        payload["route_reason"] = self.reason
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RouteDecision":
        data = dict(payload or {})
        return cls(
            route=str(data.get("route") or data.get("selected_route") or "no_search"),
            confidence=float(data.get("confidence") or 0.0),
            reason=str(data.get("reason") or data.get("route_reason") or "cached_route"),
            matched_rules=list(data.get("matched_rules") or []),
            used_llm=bool(data.get("used_llm")),
            cache_status=str(data.get("cache_status") or "miss"),
            boundary=str(data.get("boundary") or "deterministic"),
            high_stakes=bool(data.get("high_stakes")),
            routing_signals=dict(data.get("routing_signals") or data.get("signals") or {}),
        )


def _from_rule(rule: RuleDecision, *, cache_status: str, boundary: str, used_llm: bool = False) -> RouteDecision:
    route = rule.route if rule.route in VALID_ROUTES else "no_search"
    return RouteDecision(
        route=route,
        confidence=max(0.0, min(1.0, float(rule.confidence or 0.0))),
        reason=rule.reason,
        matched_rules=list(rule.matched_rules or []),
        used_llm=used_llm,
        cache_status=cache_status,
        boundary=boundary,
        high_stakes=bool(rule.high_stakes),
        routing_signals=dict(getattr(rule, "signals", {}) or {}),
    )


def _needs_global_hybrid(query: str) -> bool:
    text = str(query or "").lower()
    return bool(
        any(marker in text for marker in ("derniere", "dernière", "quelle", "最新", "是多少", "kya", "hai", " oda ", " ka "))
        or ("version" in text and re.search(r"\b(ka|kya|hai|oda|quelle|derniere)\b", text))
    )


def _is_deterministic_fast_lane(rule: RuleDecision, query: str = "") -> bool:
    route = str(rule.route or "")
    matched = set(rule.matched_rules or [])
    if route in {"fast_message", "doc_mode", "clarification"}:
        return True
    if route == "fast_search" and "freshness" in matched:
        if _needs_global_hybrid(query):
            return False
        return True
    if route == "no_search" and "definition" in matched:
        return True
    if route == "task" and rule.confidence >= 0.82:
        return True
    if bool(rule.high_stakes):
        return True
    if "role_lookup" in matched:
        return True
    return False


class RouteDecider:
    """Deterministic-first router: cache -> rules -> scoring -> tiny LLM -> safe default."""

    def __init__(
        self,
        cache: Optional[RouteCache] = None,
        llm_fallback: Optional[TinyLLMRouteFallback] = None,
        llm_timeout_seconds: float = 1.2,
    ) -> None:
        self._cache = cache or RouteCache()
        self._llm_fallback = llm_fallback or TinyLLMRouteFallback()
        self._llm_timeout_seconds = min(1.2, max(0.1, float(llm_timeout_seconds or 1.2)))
        self._global_router = GlobalHybridRouter()
        self._understanding = UniversalUnderstandingGateway()

    async def decide(self, query: str, context: Optional[Dict[str, Any]] = None) -> RouteDecision:
        context = dict(context or {})
        understanding_frame = context.get("universal_understanding_frame")
        if understanding_frame is None or not hasattr(understanding_frame, "original_query"):
            understanding_frame = self._understanding.understand(query, context=context)
        understanding_summary = frame_to_trace_summary(understanding_frame)
        context["universal_understanding"] = understanding_summary
        normalized = normalize_query(
            understanding_summary.get("normalized_query")
            or understanding_summary.get("cleaned_query")
            or query
        )

        cached_payload, cache_status = self._cache.get(normalized)
        if cached_payload:
            decision = RouteDecision.from_dict(cached_payload)
            decision.cache_status = "hit"
            return decision

        route_hint = str(understanding_summary.get("route_hint") or "").strip().lower()
        understanding_confidence = float(understanding_summary.get("confidence") or 0.0)
        if route_hint in VALID_ROUTES and understanding_confidence >= 0.68:
            decision = RouteDecision(
                route=route_hint,
                confidence=min(1.0, max(0.0, understanding_confidence)),
                reason="universal_understanding_route_hint",
                matched_rules=[
                    "universal_understanding",
                    f"intent_hint:{understanding_summary.get('intent_hint') or ''}",
                    f"relation:{understanding_summary.get('relation') or ''}",
                ],
                cache_status=cache_status,
                boundary="universal_understanding",
                high_stakes=False,
                routing_signals={"universal_understanding": understanding_summary},
            )
            self._cache.set(normalized, decision.to_dict())
            return decision

        deterministic = deterministic_route(normalized, context)
        if deterministic and deterministic.confidence >= 0.75:
            if _is_deterministic_fast_lane(deterministic, normalized):
                decision = _from_rule(deterministic, cache_status=cache_status, boundary="deterministic")
                self._cache.set(normalized, decision.to_dict())
                return decision

        hybrid = self._global_router.route(normalized, context)
        if hybrid and hybrid.confidence >= 0.68:
            decision = _from_rule(hybrid, cache_status=cache_status, boundary="global_hybrid")
            self._cache.set(normalized, decision.to_dict())
            return decision

        scored = score_routes(normalized, context)
        if scored.confidence >= 0.65:
            decision = _from_rule(scored, cache_status=cache_status, boundary="heuristic")
            self._cache.set(normalized, decision.to_dict())
            return decision

        try:
            llm_payload = await asyncio.wait_for(
                self._llm_fallback.decide(normalized, context),
                timeout=self._llm_timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError):
            llm_payload = None
        except Exception:
            llm_payload = None

        if llm_payload:
            route = str(llm_payload.get("route") or "").strip().lower()
            confidence = max(0.0, min(1.0, float(llm_payload.get("confidence") or 0.0)))
            if route in VALID_ROUTES and confidence >= 0.6:
                decision = RouteDecision(
                    route=route,
                    confidence=confidence,
                    reason=str(llm_payload.get("reason") or "llm_route_fallback"),
                    matched_rules=["llm_fallback"],
                    used_llm=True,
                    cache_status=cache_status,
                    boundary="llm_fallback",
                )
                self._cache.set(normalized, decision.to_dict())
                return decision

        fallback = safe_default_route(normalized, context)
        return _from_rule(fallback, cache_status=cache_status, boundary="safe_default")

    def decide_sync_for_tests(self, query: str, context: Optional[Dict[str, Any]] = None) -> RouteDecision:
        return asyncio.run(self.decide(query, context))
