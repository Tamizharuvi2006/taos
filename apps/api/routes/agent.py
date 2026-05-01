"""
TAOS API — Agent Route (Simplified endpoint per Backend Implementation Report).

POST /execute — Primary endpoint for the AI Dev Research Assistant.
POST /plan    — Preview execution plan without running.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from taos.apps.api.auth_context import resolve_user_id
from taos.apps.api.response_contract import normalize_contract_payload
from taos.apps.api.errors import raise_api_error
from taos.apps.api.schemas.agent import AgentRequest, AgentResponse
from taos.apps.api.schemas.trace import TraceResponse
from taos.core.chat import PersistentChatManager
from taos.config.settings import get_settings
from taos.core.fast_path import FastPathEngine
from taos.core.limits import get_quota_manager
from taos.core.performance.progress import get_tracker, register_tracker_owner
from taos.core.reliability import (
    AmbiguityFallbackHandler,
    RequestBudgetManager,
    StageBudgetManager,
    TimeoutFallbackBuilder,
    build_stage_budget_metadata,
    build_stage_timing_rows,
    summarize_latency,
)
from taos.core.routing.route_rules import deterministic_route, safe_default_route
from taos.core.security import redact_secret_text, sanitize_public_trace, validate_user_prompt
from taos.core.streaming import StreamEvent, StreamEventType, phase_to_event_type
from taos.infra.logging.logger import TAOSLogger
from taos.orchestration.route_dispatcher import RouteDispatcher
from taos.orchestration.engine import OrchestrationEngine

router = APIRouter(tags=["agent"])
_MICRO_FAST_ENGINE = FastPathEngine()
_CHAT_MANAGERS: Dict[str, PersistentChatManager] = {}
_AMBIGUITY_FALLBACK = AmbiguityFallbackHandler()
_TIMEOUT_FALLBACK = TimeoutFallbackBuilder()
_ROUTE_DISPATCHER = RouteDispatcher()


def _get_chat_manager(user_id: str) -> PersistentChatManager:
    uid = str(user_id or "default")
    if uid not in _CHAT_MANAGERS:
        _CHAT_MANAGERS[uid] = PersistentChatManager(user_id=uid)
    return _CHAT_MANAGERS[uid]


def _normalize_doc_ids(doc_ids: List[str]) -> List[str]:
    seen = set()
    cleaned: List[str] = []
    for doc_id in list(doc_ids or []):
        did = str(doc_id or "").strip()
        if not did:
            continue
        key = did.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(did)
    return cleaned


async def _resolve_doc_ids_for_request(*, user_id: str, chat_id: Optional[str], request_doc_ids: List[str]) -> List[str]:
    explicit = _normalize_doc_ids(request_doc_ids)
    if explicit:
        return explicit
    cid = str(chat_id or "").strip()
    if not cid:
        return []
    try:
        chat = await _get_chat_manager(user_id).get_chat(cid)
    except Exception:
        return []
    if not chat:
        return []
    return _normalize_doc_ids(list(chat.get("doc_ids") or []))


def _build_trace(trace_value: Any, request_id: str) -> Optional[TraceResponse]:
    if trace_value is None:
        return None
    if isinstance(trace_value, TraceResponse):
        return trace_value
    if isinstance(trace_value, list):
        payload: Any = {"request_id": request_id, "events": trace_value, "count": len(trace_value)}
    elif isinstance(trace_value, dict):
        payload = dict(trace_value)
        if "events" not in payload and "traces" in payload:
            payload["events"] = payload.pop("traces")
        if "request_id" not in payload:
            payload["request_id"] = request_id
        if "count" not in payload and isinstance(payload.get("events"), list):
            payload["count"] = len(payload["events"])
    else:
        return None

    _enrich_public_trace(payload)
    payload = sanitize_public_trace(payload)
    try:
        return TraceResponse.model_validate(payload)
    except Exception:
        return None


def _enrich_public_trace(payload: Dict[str, Any]) -> None:
    route = str(payload.get("public_route_label") or payload.get("route_label") or payload.get("route") or "").strip()
    route_decision = dict(payload.get("route_decision") or {})
    route_boundary = dict(payload.get("route_boundary_summary") or {})
    timing = dict(payload.get("timing") or {})
    if not route:
        route = str(payload.get("selected_route") or route_decision.get("selected_route") or route_decision.get("route") or route_boundary.get("selected_route") or route_boundary.get("route") or "").strip()
    selected_route = str(
        payload.get("selected_route")
        or route_decision.get("selected_route")
        or route_boundary.get("selected_route")
        or route
        or ""
    ).strip()
    original_route_hint = str(
        payload.get("original_route_hint")
        or route_boundary.get("route")
        or route_decision.get("route")
        or route
        or ""
    ).strip()
    owner = str(
        payload.get("route_owner")
        or payload.get("owner")
        or route_boundary.get("owner")
        or route_decision.get("route_owner")
        or ""
    ).strip()
    if selected_route:
        payload.setdefault("selected_route", selected_route)
    if route:
        payload.setdefault("public_route_label", route)
        payload.setdefault("route_label", route)
    if original_route_hint:
        payload.setdefault("original_route_hint", original_route_hint)
    if owner:
        payload.setdefault("route_owner", owner)
        payload.setdefault("owner", owner)
    payload.setdefault("stage_budgets", build_stage_budget_metadata(route))
    payload.setdefault("stage_timings", build_stage_timing_rows(timing, route))
    payload.setdefault("public_summary", _build_public_trace_summary(payload))


def _build_public_trace_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    trace = dict(payload or {})
    route_decision = dict(trace.get("route_decision") or {})
    route_boundary = dict(trace.get("route_boundary_summary") or {})
    trust = dict(trace.get("trust_block") or {})
    timing = dict(trace.get("timing") or {})
    evidence = dict(trace.get("evidence_matrix_summary") or {})
    freshness = dict(trace.get("freshness_summary") or trust.get("freshness_summary") or {})
    conflict = dict(trace.get("conflict_summary") or trust.get("conflict_summary") or {})
    evidence_stats = dict(trace.get("evidence_stats") or {})
    provider_health = dict(trace.get("provider_health") or {})
    stage_timings = list(trace.get("stage_timings") or build_stage_timing_rows(timing, trace.get("route_label")))
    route_label = str(
        trace.get("public_route_label")
        or trace.get("route_label")
        or trace.get("selected_route")
        or route_decision.get("selected_route")
        or route_boundary.get("selected_route")
        or route_decision.get("route")
        or route_boundary.get("route")
        or "standard_task"
    ).strip()
    selected_route = str(
        trace.get("selected_route")
        or route_decision.get("selected_route")
        or route_boundary.get("selected_route")
        or route_label
    ).strip()
    original_route_hint = str(
        trace.get("original_route_hint")
        or route_boundary.get("route")
        or route_decision.get("route")
        or route_label
    ).strip()
    owner = str(
        trace.get("route_owner")
        or trace.get("owner")
        or route_boundary.get("owner")
        or route_decision.get("route_owner")
        or ("search_lite" if route_label == "fast_search" else "")
        or trace.get("planner_path")
        or "unknown"
    ).strip()
    sources_found = int(
        evidence_stats.get("sources_found")
        or evidence_stats.get("candidate_count")
        or trust.get("source_count")
        or 0
    )
    sources_used = int(
        evidence_stats.get("sources_used")
        or evidence_stats.get("usable_sources_count")
        or evidence_stats.get("selected_count")
        or trust.get("source_count")
        or 0
    )
    coverage = float(
        evidence.get("coverage")
        or evidence.get("citation_coverage")
        or trust.get("citation_coverage")
        or evidence_stats.get("coverage")
        or 0.0
    )
    unsupported = int(
        evidence.get("unsupported_claims")
        or evidence.get("claims_unsupported")
        or trust.get("unsupported_claims")
        or evidence_stats.get("unsupported_claims")
        or 0
    )
    public = {
        "route": {
            "label": route_label,
            "owner": owner,
            "selected_route": selected_route,
            "original_route_hint": original_route_hint,
            "confidence": route_decision.get("confidence") or trace.get("route_confidence"),
            "reason": _clean_public_text(route_decision.get("reason") or trace.get("policy_reason") or route_boundary.get("boundary") or ""),
        },
        "execution": {
            "path": trace.get("planner_path") or owner,
            "planner_used": bool(route_boundary.get("planner_allowed") or route_boundary.get("will_use_planner") or route_boundary.get("fsm_allowed")),
            "llm_route_fallback_used": bool(route_decision.get("llm_fallback_used") or route_decision.get("llm_used")),
        },
        "research": {
            "queries_run": int(evidence_stats.get("queries_run") or evidence_stats.get("query_count") or 0),
            "sources_found": sources_found,
            "sources_used": sources_used,
            "official_sources": int(evidence_stats.get("official_source_count") or trust.get("official_source_count") or 0),
            "coverage": round(max(0.0, min(1.0, coverage)), 3),
            "answer_mode": str(trust.get("answer_mode") or evidence_stats.get("answer_mode") or ""),
        },
        "trust": {
            "level": str(trust.get("trust_level") or trust.get("confidence_label") or ""),
            "freshness": str(freshness.get("freshness_mode") or trust.get("freshness") or ""),
            "agreement": str(conflict.get("agreement_level") or ("mixed" if conflict.get("groups") else "unknown")),
            "unsupported_claims": unsupported,
            "conflict_detected": bool(conflict.get("conflict_detected") or conflict.get("groups")),
        },
        "latency": summarize_latency(timing, stage_timings),
    }
    if provider_health:
        public["provider_health"] = provider_health
    return sanitize_public_trace(public)


def _clean_public_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", redact_secret_text(value)).strip()
    if not text:
        return ""
    if text == "[redacted]":
        return "internal detail hidden"
    internal_patterns = ("traceback", "exception", "stack", "apikey", "api key", "secret")
    if any(term in text.lower() for term in internal_patterns):
        return "internal detail hidden"
    return text[:240]


def _build_minimal_trace_from_payload(payload: Dict[str, Any], request_id: str) -> Optional[Dict[str, Any]]:
    metadata = dict((payload or {}).get("metadata") or {})
    route = str(
        payload.get("public_route_label")
        or payload.get("route")
        or payload.get("route_label")
        or metadata.get("public_route_label")
        or metadata.get("route_label")
        or ""
    ).strip()
    query_kind = str(payload.get("query_kind") or metadata.get("query_kind") or "").strip()
    policy_reason = str(payload.get("policy_reason") or metadata.get("policy_reason") or "").strip()
    verification_state = str(payload.get("verification_state") or metadata.get("verification_state") or "").strip()
    if not route and not query_kind and not policy_reason:
        return None
    planner_path = "fast_search" if route == "fast_search" else str(metadata.get("planner_path") or route or "direct")
    owner = "search_lite" if route == "fast_search" else str(metadata.get("route_owner") or "")
    dag_name = "search_lite" if route == "fast_search" else metadata.get("dag_name")
    route_boundary_summary = {
        "route": route or planner_path,
        "selected_route": route or planner_path,
        "owner": owner or "unknown",
        "boundary": "api_payload_recovered",
        "will_use_search_lite": route == "fast_search",
        "will_use_research_pipeline": False,
        "will_use_planner": False,
        "planner_allowed": False,
        "fsm_allowed": False,
        "web_search_allowed": route == "fast_search",
        "research_allowed": False,
        "doc_pipeline_allowed": route == "doc_mode",
    }
    route_decision = {
        "route": route or planner_path,
        "selected_route": route or planner_path,
        "route_owner": owner or "unknown",
        "policy_reason": policy_reason or None,
        "route_reason": policy_reason or metadata.get("route_reason") or None,
        "confidence": metadata.get("route_confidence"),
    }
    return {
        "request_id": request_id,
        "intent": payload.get("intent"),
        "mode": payload.get("mode"),
        "query_kind": query_kind or None,
        "verification_state": verification_state or None,
        "policy_reason": policy_reason or None,
        "route_label": route or None,
        "public_route_label": route or None,
        "selected_route": route or planner_path,
        "original_route_hint": route or planner_path,
        "owner": owner or "unknown",
        "route_owner": owner or "unknown",
        "route_source": metadata.get("route_source"),
        "route_confidence": metadata.get("route_confidence"),
        "route_decision": route_decision,
        "route_boundary_summary": route_boundary_summary,
        "planner_path": planner_path,
        "dag_name": dag_name,
        "pipeline_stages": ["Query", "Search", "Rank", "Answer", "Trust"] if route == "fast_search" else ["Query", "Generate", "Trust"],
        "fsm_transitions": [],
        "steps": [],
        "fallback_used": False,
        "freshness_check": {"status": "not_applicable", "stale_phrase_detected": False},
        "freshness_summary": payload.get("freshness_summary"),
        "evidence_matrix_summary": payload.get("evidence_matrix_summary"),
        "confidence": payload.get("confidence"),
        "trust_block": payload.get("trust_block"),
    }


def _agent_response_payload(response: AgentResponse) -> Dict[str, Any]:
    payload = response.model_dump(mode="json")
    if payload.get("trace") is None:
        payload.pop("trace", None)
    return payload


def _build_agent_response_from_payload(
    payload: Dict[str, Any],
    *,
    request_id: str,
    include_trace: bool,
) -> AgentResponse:
    normalized = normalize_contract_payload(dict(payload or {}))
    trace_value = normalized.get("trace") or normalized.get("traces")
    if include_trace and not trace_value:
        trace_value = _build_minimal_trace_from_payload(normalized, request_id)
    return AgentResponse(
        answer=str(normalized.get("answer") or ""),
        direct_answer=str(normalized.get("direct_answer") or normalized.get("answer") or ""),
        key_points=list(normalized.get("key_points") or []),
        intent=str(normalized.get("intent") or "task"),
        domain=str(normalized.get("domain") or "general"),
        mode=str(normalized.get("mode") or ("fast" if normalized.get("fast_path") else "standard")),
        confidence=float(normalized.get("confidence") or 0.0),
        sources=list(normalized.get("sources") or []),
        fast_path=bool(normalized.get("fast_path", False)),
        evaluation=normalized.get("evaluation"),
        steps_executed=int(normalized.get("steps_executed", 0) or 0),
        cost=float(normalized.get("cost") or normalized.get("total_cost") or 0.0),
        elapsed_ms=round(float(normalized.get("elapsed_ms") or 0.0), 2),
        error=normalized.get("error"),
        request_id=request_id,
        replan_count=int(normalized.get("replan_count", 0) or 0),
        trust_block=normalized.get("trust_block"),
        trust_badges=list(normalized.get("trust_badges") or []),
        uncertainty_box=normalized.get("uncertainty_box"),
        source_cards=list(normalized.get("source_cards") or []),
        answer_sections=list(normalized.get("answer_sections") or []),
        sections=list(normalized.get("sections") or []),
        route=str(normalized.get("route") or "standard_task"),
        warnings=list(normalized.get("warnings") or []),
        evidence_matrix_summary=normalized.get("evidence_matrix_summary"),
        freshness_summary=normalized.get("freshness_summary"),
        evidence_selection_summary=normalized.get("evidence_selection_summary"),
        citation_plan_summary=normalized.get("citation_plan_summary"),
        diversity_summary=normalized.get("diversity_summary"),
        conflict_summary=normalized.get("conflict_summary"),
        high_stakes_summary=normalized.get("high_stakes_summary"),
        cache_summary=normalized.get("cache_summary"),
        metadata=dict(normalized.get("metadata") or {}),
        frontend_hints=normalized.get("frontend_hints"),
        trace=_build_trace(trace_value, request_id) if include_trace else None,
    )


def _request_timeout_seconds(query: str, settings) -> float:
    q = (query or "").lower()
    research_markers = (
        "research",
        "reserch",
        "analyze",
        "analysis",
        "comprehensive",
        "investigate",
        "current status",
        "latest status",
        "war",
        "conflict",
        "timeline",
        "report",
    )
    standard_cap_seconds = 45.0
    research_cap_seconds = 75.0
    if any(m in q for m in research_markers):
        return float(min(research_cap_seconds, max(settings.max_request_time_seconds, settings.max_research_time_seconds)))
    return float(min(standard_cap_seconds, settings.max_request_time_seconds))


def _tracker_timeout_stage(request_id: str) -> str:
    tracker = get_tracker(request_id)
    if not tracker:
        return "runtime"
    phase = getattr(getattr(tracker, "current", None), "phase", None)
    if hasattr(phase, "value"):
        return str(phase.value or "runtime")
    text = str(phase or "").strip().lower()
    return text or "runtime"


def _first_event_latency_ms(stream_started_at: float, first_visible_sent_at: Optional[float]) -> float:
    if first_visible_sent_at is None:
        return 0.0
    return max(0.0, (first_visible_sent_at - stream_started_at) * 1000)


def _attach_stream_runtime_trace(
    payload: Dict[str, Any],
    *,
    stream_started_at: float,
    first_visible_sent_at: Optional[float],
    budget_exceeded: bool = False,
    timeout_stage: str = "",
    partial_answer_used: bool = False,
) -> Dict[str, Any]:
    trace_blob = payload.get("trace")
    if not isinstance(trace_blob, dict):
        trace_blob = {}
        payload["trace"] = trace_blob
    timing = trace_blob.setdefault("timing", {})
    if not isinstance(timing, dict):
        timing = {}
        trace_blob["timing"] = timing
    first_latency_ms = _first_event_latency_ms(stream_started_at, first_visible_sent_at)
    trace_blob["streaming_started_at"] = round(float(stream_started_at or 0.0), 6)
    trace_blob["first_event_latency_ms"] = round(first_latency_ms, 2)
    trace_blob["budget_exceeded"] = bool(budget_exceeded)
    trace_blob["timeout_stage"] = str(timeout_stage or "").strip() or None
    trace_blob["partial_answer_used"] = bool(partial_answer_used)
    timing["first_event_latency_ms"] = round(first_latency_ms, 2)
    timing["streaming_started_at"] = round(float(stream_started_at or 0.0), 6)
    return payload


def _is_truthy(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on", "enabled"}


def _quota_route_owner_hint(query: str, *, doc_context_active: bool) -> tuple[str, str]:
    normalized = str(query or "").strip().lower()
    context = {"has_active_doc": bool(doc_context_active)}
    decision = deterministic_route(normalized, context)
    if decision is None:
        decision = safe_default_route(normalized, context)
    route = str(getattr(decision, "route", "") or "").strip()
    owner = _ROUTE_DISPATCHER.owner_for_route(route) if route else ""
    return route, owner


def _perf_test_mode_requested(raw_request: Request, settings: Any) -> bool:
    # Dev-only explicit path; production is rejected inside quota manager.
    return bool(
        _is_truthy(raw_request.headers.get("X-Perf-Test-Mode"))
        or _is_truthy(raw_request.query_params.get("perf_test_mode"))
        or bool(getattr(settings, "perf_test_mode", False))
    )


async def _include_trace_requested(raw_request: Request, request_value: bool) -> bool:
    if bool(request_value):
        return True
    query_value = str(raw_request.query_params.get("include_trace") or raw_request.query_params.get("includeTrace") or "").strip().lower()
    if query_value in {"1", "true", "yes", "on"}:
        return True
    header_value = str(raw_request.headers.get("X-Include-Trace") or "").strip().lower()
    if header_value in {"1", "true", "yes", "on"}:
        return True
    try:
        body = await raw_request.json()
    except Exception:
        body = {}
    if isinstance(body, dict):
        value = body.get("include_trace", body.get("includeTrace"))
        if isinstance(value, bool):
            return value
        if str(value or "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _build_micro_fast_trust_block() -> Dict[str, Any]:
    return {
        "freshness": "Not Applicable",
        "evidence": "Minimal",
        "execution_path": "Direct",
        "fallback_used": False,
        "confidence": "High",
        "source_count": 0,
        "usable_sources_count": 0,
        "rejected_sources_count": 0,
        "official_source_count": 0,
        "agreement": "unknown",
        "agreement_score": 0.0,
        "conflict_detected": False,
        "stale_detected": False,
        "signal": "clean",
        "event_agreement": "not_applicable",
        "attribution_agreement": "not_applicable",
        "domain_diversity": 0.0,
        "extraction_quality": 0.0,
        "official_source_required": False,
        "official_source_found": False,
        "high_stakes_mode": False,
        "uncertainty_flags": [],
    }


def _build_micro_fast_trace(
    *,
    request_id: str,
    doc_context_active: bool,
    total_ms: float,
    trust_block: Dict[str, Any],
) -> Dict[str, Any]:
    safe_total_ms = round(max(0.0, float(total_ms or 0.0)), 2)
    return {
        "request_id": request_id,
        "intent": "simple_lookup",
        "mode": "fast",
        "doc_context_active": bool(doc_context_active),
        "planner_path": "direct",
        "dag_name": None,
        "pipeline_stages": ["Query", "Respond", "Trust"],
        "fsm_transitions": [],
        "steps": [
            {
                "id": "direct_1",
                "type": "reason",
                "status": "success",
                "tool": "small_talk_rule",
                "retries": 0,
                "latency_ms": safe_total_ms,
                "summary": "Answered via small_talk_rule without full planning.",
                "error": None,
                "nodes": [],
            }
        ],
        "fallback_used": False,
        "fallback_reason": None,
        "freshness_check": {
            "status": "not_applicable",
            "stale_phrase_detected": False,
            "note": "Micro-fast response path (no external retrieval).",
        },
        "confidence": 0.99,
        "timing": {
            "route_ms": 0.0,
            "llm_ms": 0.0,
            "llm_calls": 0,
            "total_ms": safe_total_ms,
            "time_to_first_token_ms": safe_total_ms,
            "time_to_final_ms": safe_total_ms,
        },
        "trust_block": trust_block,
    }


def _build_micro_fast_payload(
    *,
    answer: str,
    request_id: str,
    doc_context_active: bool,
    elapsed_ms: float,
    include_trace: bool,
) -> Dict[str, Any]:
    trust_block = _build_micro_fast_trust_block()
    payload: Dict[str, Any] = {
        "request_id": request_id,
        "answer": answer,
        "result": answer,
        "formatted_response": answer,
        "direct_answer": answer,
        "key_points": [],
        "intent": "simple_lookup",
        "domain": "general",
        "mode": "fast",
        "confidence": 0.99,
        "sources": [],
        "fast_path": True,
        "evaluation": None,
        "steps_executed": 0,
        "cost": 0.0,
        "elapsed_ms": round(max(0.0, float(elapsed_ms or 0.0)), 2),
        "replan_count": 0,
        "route_label": "fast_message",
        "route_source": "heuristic",
        "route_confidence": 1.0,
        "doc_context_active": bool(doc_context_active),
        "trust_block": trust_block,
    }
    if include_trace:
        payload["trace"] = _build_micro_fast_trace(
            request_id=request_id,
            doc_context_active=doc_context_active,
            total_ms=elapsed_ms,
            trust_block=trust_block,
        )
    return payload


def _infer_source_type(url: str, source_row: Optional[Dict[str, Any]] = None) -> str:
    row = source_row or {}
    tier = str(row.get("tier") or "").strip().lower()
    if tier == "official":
        return "official"
    if str(url or "").lower().startswith("internal://doc/"):
        return "document"
    domain = _extract_domain(url).lower()
    if domain in {"npmjs.com", "pypi.org", "crates.io", "rubygems.org", "packagist.org"}:
        return "official"
    return "reporting"


def _extract_domain(url: str) -> str:
    try:
        parsed = urlparse(str(url or "").strip())
        return (parsed.netloc or "").replace("www.", "") or "unknown"
    except Exception:
        return "unknown"


def _build_source_cards(sources: List[Any], trust_block: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for idx, item in enumerate(list(sources or [])[:8], start=1):
        row: Dict[str, Any] = item if isinstance(item, dict) else {}
        link = str(row.get("link") or row.get("url") or (item if isinstance(item, str) else "")).strip()
        if not link:
            continue
        domain = _extract_domain(link)
        title = str(row.get("title") or row.get("provider") or domain or f"Source {idx}").strip()
        published_at = row.get("date_hint") or row.get("published_at") or row.get("date")
        source_type = _infer_source_type(link, row)
        quality_hint = None
        if source_type == "official":
            quality_hint = "Official source"
        elif source_type == "document":
            quality_hint = "Uploaded document evidence"
        elif trust_block and str(trust_block.get("evidence") or "").lower() in {"strong", "moderate"}:
            quality_hint = "Corroborated reporting"
        cards.append(
            {
                "title": title,
                "url": link,
                "domain": domain,
                "published_at": str(published_at) if published_at else None,
                "source_type": source_type,
                "quality_hint": quality_hint,
            }
        )
    return cards


def _build_trust_badges(trust_block: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    trust = dict(trust_block or {})
    if not trust:
        return []
    conflict = bool(trust.get("conflict_detected"))
    high_stakes = bool(trust.get("high_stakes_mode"))
    unsupported_claims = int(trust.get("unsupported_claims") or 0)
    citation_coverage = float(trust.get("citation_coverage") or 0.0)
    weak_coverage = citation_coverage > 0 and citation_coverage < 0.55
    badges: List[Dict[str, str]] = [
        {
            "key": "trust",
            "label": "Trust",
            "value": str(trust.get("confidence") or "Unknown"),
            "tone": "positive" if str(trust.get("confidence") or "").lower() == "high" else "neutral",
        },
        {
            "key": "freshness",
            "label": "Freshness",
            "value": str(trust.get("freshness") or "Unknown"),
            "tone": "neutral",
        },
        {
            "key": "agreement",
            "label": "Agreement",
            "value": str(trust.get("agreement") or "unknown"),
            "tone": "warning" if conflict else "neutral",
        },
        {
            "key": "conflict",
            "label": "Conflict",
            "value": "Detected" if conflict else "Not detected",
            "tone": "warning" if conflict else "positive",
        },
        {
            "key": "high_stakes",
            "label": "High-stakes",
            "value": "Yes" if high_stakes else "No",
            "tone": "warning" if high_stakes else "neutral",
        },
    ]
    if citation_coverage > 0:
        badges.append(
            {
                "key": "coverage",
                "label": "Coverage",
                "value": f"{round(citation_coverage * 100)}%",
                "tone": "warning" if weak_coverage else "positive",
            }
        )
    if unsupported_claims > 0:
        badges.append(
            {
                "key": "unsupported_claims",
                "label": "Unsupported",
                "value": str(unsupported_claims),
                "tone": "warning",
            }
        )
    return badges


def _build_uncertainty_box(answer: str, trust_block: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    trust = dict(trust_block or {})
    answer_lower = str(answer or "").lower()
    conflict = bool(trust.get("conflict_detected")) or "sources disagree" in answer_lower
    high_stakes = bool(trust.get("high_stakes_mode"))
    weak_signal = str(trust.get("signal") or "").lower() in {"partial_conflict", "conflicting", "candidate_only"}
    not_verified = str(trust.get("verification_state") or "").lower() == "not_verified"
    official_missing = bool(trust.get("official_source_required")) and not bool(trust.get("official_source_found"))
    if not (conflict or high_stakes or weak_signal or official_missing or not_verified):
        return None

    messages: List[str] = ["This is not fully confirmed yet."]
    if not_verified:
        messages.append("Candidate sources were found, but strict verification could not confirm the claim.")
    if conflict:
        messages.append("Sources disagree on key details.")
    if high_stakes or official_missing:
        messages.append("Do not act on this alone without official confirmation.")
    return {
        "level": "caution",
        "title": "What to treat carefully",
        "messages": messages,
        "high_stakes": high_stakes,
    }


def _title_for_section_key(key: str) -> str:
    return {
        "rumour_status": "Rumour status",
        "claim_status": "Claim status",
        "best_supported_status": "Best-supported status",
        "best_supported_answer": "Best-supported answer",
        "related_evidence": "Related evidence",
        "what_is_confirmed": "What is confirmed",
        "what_is_not_confirmed": "What is not confirmed",
        "what_this_does_not_prove": "What this does NOT prove",
        "what_may_be_causing_confusion": "What may be causing confusion",
        "what_i_could_not_verify": "What I could not verify",
        "what_i_checked": "What I checked",
        "best_next_checks": "Best next checks",
        "latest_version": "Latest version",
        "source_of_record": "Source-of-record",
        "best_supported_candidate": "Best-supported candidate",
        "why_this_candidate": "Why this candidate",
        "other_possible_matches": "Other possible matches",
        "quick_verdict": "Quick verdict",
        "comparison": "Comparison",
        "best_choice_by_use_case": "Best choice by use case",
        "trade_offs": "Trade-offs",
        "likely_cause": "Likely cause",
        "fix": "Fix",
        "why_it_works": "Why it works",
        "if_it_still_fails": "If it still fails",
        "simple_explanation": "Simple explanation",
        "example": "Example",
        "common_mistake": "Common mistake",
        "quick_recap": "Quick recap",
        "sources_checked": "Sources checked",
        "sources": "Sources",
        "confidence": "Confidence",
        "what_happened": "What happened",
        "key_evidence": "Key evidence",
        "what_is_unclear": "What is still unclear",
        "bottom_line": "Bottom line",
        "follow_ups": "Follow-ups",
        "answer": "Answer",
    }.get(key, key.replace("_", " ").title() or "Answer")


def _parse_answer_sections(answer: str) -> List[Dict[str, Any]]:
    text = str(answer or "").strip()
    if not text:
        return []
    section_aliases = {
        "answer": "what_happened",
        "what happened": "what_happened",
        "best-supported answer": "best_supported_answer",
        "best-supported status": "best_supported_status",
        "best-supported finding": "best_supported_status",
        "best-supported related finding": "best_supported_status",
        "rumour status": "rumour_status",
        "claim status": "claim_status",
        "key evidence": "key_evidence",
        "evidence": "key_evidence",
        "key points": "key_evidence",
        "related evidence": "related_evidence",
        "closest related evidence": "related_evidence",
        "what is confirmed": "what_is_confirmed",
        "what is not confirmed": "what_is_not_confirmed",
        "what i could not verify": "what_i_could_not_verify",
        "what i checked": "what_i_checked",
        "best next checks": "best_next_checks",
        "sources checked": "sources_checked",
        "source-of-record": "source_of_record",
        "latest version": "latest_version",
        "best-supported candidate": "best_supported_candidate",
        "why this candidate": "why_this_candidate",
        "other possible matches": "other_possible_matches",
        "quick verdict": "quick_verdict",
        "comparison": "comparison",
        "best choice by use case": "best_choice_by_use_case",
        "trade-offs": "trade_offs",
        "likely cause": "likely_cause",
        "fix": "fix",
        "why it works": "why_it_works",
        "if it still fails": "if_it_still_fails",
        "simple explanation": "simple_explanation",
        "example": "example",
        "common mistake": "common_mistake",
        "quick recap": "quick_recap",
        "what this does not prove": "what_this_does_not_prove",
        "what may be causing confusion": "what_may_be_causing_confusion",
        "likely confusion": "what_may_be_causing_confusion",
        "what's still unclear": "what_is_unclear",
        "what is still unclear": "what_is_unclear",
        "what is uncertain or disputed": "what_is_unclear",
        "bottom line": "bottom_line",
        "next useful follow-ups": "follow_ups",
        "next useful follow-up": "follow_ups",
        "next useful moves": "follow_ups",
        "follow-ups": "follow_ups",
    }
    sections: Dict[str, Dict[str, Any]] = {}
    current = "answer"
    sections[current] = {"key": "answer", "title": "Answer", "content": "", "bullets": []}

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        normalized = line.lower().rstrip(":")
        mapped = section_aliases.get(normalized)
        if mapped:
            current = mapped
            sections.setdefault(
                current,
                {"key": current, "title": _title_for_section_key(current), "content": "", "bullets": []},
            )
            continue
        if line.startswith("- "):
            sections[current]["bullets"].append(line[2:].strip())
        else:
            prior = sections[current]["content"]
            sections[current]["content"] = (prior + "\n" + line).strip() if prior else line
    ordered = [
        "rumour_status",
        "claim_status",
        "best_supported_status",
        "best_supported_answer",
        "related_evidence",
        "what_is_confirmed",
        "what_is_not_confirmed",
        "what_this_does_not_prove",
        "what_may_be_causing_confusion",
        "what_i_could_not_verify",
        "what_i_checked",
        "best_next_checks",
        "latest_version",
        "source_of_record",
        "best_supported_candidate",
        "why_this_candidate",
        "other_possible_matches",
        "quick_verdict",
        "comparison",
        "best_choice_by_use_case",
        "trade_offs",
        "likely_cause",
        "fix",
        "why_it_works",
        "if_it_still_fails",
        "simple_explanation",
        "example",
        "common_mistake",
        "quick_recap",
        "confidence",
        "sources_checked",
        "sources",
        "what_happened",
        "key_evidence",
        "what_is_unclear",
        "bottom_line",
        "follow_ups",
        "answer",
    ]
    result: List[Dict[str, Any]] = []
    for key in ordered:
        data = sections.get(key)
        if not data:
            continue
        if not data["content"] and not data["bullets"]:
            continue
        result.append(data)
    for key, data in sections.items():
        if key in ordered:
            continue
        if not data["content"] and not data["bullets"]:
            continue
        result.append(data)
    return result


def _progress_stages_for_route(route_label: str) -> List[str]:
    route = _normalize_progress_route(route_label)
    if route in {"deep_research", "deep_search"}:
        return [
            "Searching sources",
            "Reading useful pages",
            "Comparing evidence",
            "Preparing answer",
        ]
    if route == "news_search":
        return [
            "Searching sources",
            "Reading useful pages",
            "Comparing evidence",
            "Preparing answer",
        ]
    if route == "official_search":
        return [
            "Finding official sources",
            "Verifying source-of-record evidence",
            "Preparing answer",
        ]
    if route == "comparison_search":
        return [
            "Searching both sides",
            "Comparing evidence",
            "Preparing answer",
        ]
    if route == "entity_lookup":
        return [
            "Checking company sources",
            "Verifying role claim",
            "Preparing answer",
        ]
    if route == "fast_search":
        return [
            "Checking the best live source",
            "Verifying the answer",
            "Preparing answer",
        ]
    if route == "no_search":
        return [
            "Answering directly",
            "Preparing answer",
        ]
    if route == "doc_mode":
        return [
            "Reading your document",
            "Drafting answer",
            "Preparing answer",
        ]
    if route == "clarification":
        return [
            "Clarifying request",
            "Preparing answer",
        ]
    if route == "fast_message":
        return [
            "Quick reply",
            "Preparing answer",
        ]
    return [
        "Drafting answer",
        "Preparing answer",
    ]


def _build_frontend_hints(
    route_label: str,
    trust_block: Optional[Dict[str, Any]],
    *,
    query_kind: Optional[str] = None,
    verification_state: Optional[str] = None,
    policy_reason: Optional[str] = None,
) -> Dict[str, Any]:
    trust = dict(trust_block or {})
    return {
        "route_label": str(route_label or "standard_task"),
        "progress_stages": _progress_stages_for_route(route_label),
        "high_stakes": bool(trust.get("high_stakes_mode")),
        "official_source_required": bool(trust.get("official_source_required")),
        "official_source_found": bool(trust.get("official_source_found")),
        "query_kind": str(query_kind).strip() if str(query_kind or "").strip() else None,
        "verification_state": str(verification_state).strip() if str(verification_state or "").strip() else None,
        "policy_reason": str(policy_reason).strip() if str(policy_reason or "").strip() else None,
    }


def _enrich_frontend_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    answer = str(payload.get("answer") or payload.get("formatted_response") or payload.get("result") or "")
    trust_block = payload.get("trust_block")
    route_label = str(payload.get("route_label") or "")
    trace = payload.get("trace")
    if (not route_label) and isinstance(trace, dict):
        route_label = str(trace.get("route_label") or "")
    if not route_label:
        route_label = "standard_task"
    query_kind = payload.get("query_kind")
    verification_state = payload.get("verification_state")
    policy_reason = payload.get("policy_reason")
    route_decision = payload.get("route_decision")
    trace = payload.get("trace")
    if not query_kind and isinstance(trace, dict):
        query_kind = trace.get("query_kind")
    if not verification_state and isinstance(trace, dict):
        verification_state = trace.get("verification_state")
    if not policy_reason and isinstance(trace, dict):
        policy_reason = trace.get("policy_reason")
    if isinstance(route_decision, dict):
        if not policy_reason:
            policy_reason = route_decision.get("policy_reason") or route_decision.get("route_reason")
    if str(query_kind or "").strip().lower() == "entity_lookup":
        route_label = "entity_lookup"
        payload["route_label"] = "entity_lookup"
    sources = list(payload.get("sources") or [])
    payload["trust_badges"] = _build_trust_badges(trust_block)
    payload["uncertainty_box"] = _build_uncertainty_box(answer, trust_block)
    payload["source_cards"] = _build_source_cards(sources, trust_block)
    payload["answer_sections"] = _parse_answer_sections(answer)
    evidence_summary = payload.get("evidence_matrix_summary")
    if not evidence_summary and isinstance(trust_block, dict):
        evidence_summary = trust_block.get("evidence_matrix_summary")
    if evidence_summary:
        payload["evidence_matrix_summary"] = evidence_summary
    warnings = list(payload.get("warnings") or [])
    if isinstance(trust_block, dict):
        unsupported_claims = int(trust_block.get("unsupported_claims") or 0)
        coverage = float(trust_block.get("citation_coverage") or 0.0)
        if unsupported_claims > 0:
            warnings.append(
                f"{unsupported_claims} claim{'s are' if unsupported_claims != 1 else ' is'} not strongly supported by sources."
            )
        if coverage > 0 and coverage < 0.55:
            warnings.append("Citation coverage is weak; confidence was reduced automatically.")
        if bool(trust_block.get("stale_detected")):
            warnings.append("Some supporting evidence may be stale.")
        if bool(trust_block.get("conflict_detected")):
            warnings.append("Sources conflict on key details.")
    payload["warnings"] = list(dict.fromkeys([str(w).strip() for w in warnings if str(w).strip()]))
    payload["frontend_hints"] = _build_frontend_hints(
        route_label,
        trust_block,
        query_kind=query_kind,
        verification_state=verification_state,
        policy_reason=policy_reason,
    )
    return payload


def _configure_engine_for_route(engine: OrchestrationEngine, route_hint: str) -> None:
    route = str(route_hint or "").strip().lower()
    if route == "entity_lookup":
        engine._settings.entity_lookup_v1_enabled = True


def _resolved_answer_text(raw_result: Dict[str, Any]) -> str:
    return str(
        raw_result.get("formatted_response")
        or raw_result.get("answer")
        or raw_result.get("result")
        or raw_result.get("direct_answer")
        or ""
    ).strip()


async def _run_entity_lookup_api_handoff(
    *,
    engine: OrchestrationEngine,
    query: str,
    request_id: str,
    user_id: str,
    include_trace: bool,
    doc_context_active: bool,
) -> Dict[str, Any]:
    engine._settings.entity_lookup_v1_enabled = True
    engine._reset_execution_trace(
        request_id=request_id,
        goal=query,
        include_trace=True,
    )
    classification = engine._intent_classifier.classify(
        query,
        has_context=False,
        has_active_doc=bool(doc_context_active),
    )
    route_boundary_summary = engine._build_route_boundary_summary(
        phase107_route="entity_lookup",
        selected_route="entity_lookup",
        route_owner="entity_lookup_pipeline",
        boundary="api_route_hint_override",
        used_llm=False,
    )
    route_decision = {
        "route": "entity_lookup",
        "selected_route": "entity_lookup",
        "phase107_route": "entity_lookup",
        "route_owner": "entity_lookup_pipeline",
        "policy_reason": "api_route_hint_override",
        "route_reason": "api_route_hint_override",
        "rerouted": True,
        "verification_status": "passed",
        "confidence": 1.0,
    }
    if not isinstance(classification.metadata, dict):
        classification.metadata = {}
    classification.metadata.update(
        {
            "route_label": "entity_lookup",
            "route_source": "api_route_hint_override",
            "route_confidence": 1.0,
            "query_kind": "entity_lookup",
            "route_decision": route_decision,
            "route_boundary_summary": route_boundary_summary,
            "policy_override_reasons": ["api_entity_lookup_handoff"],
            "doc_context_active": bool(doc_context_active),
            "routing_profile": {
                "query_kind": "entity_lookup",
                "grounding_need": "web",
                "context_required": False,
                "risk_level": "low",
            },
        }
    )
    engine._set_trace_value("route_label", "entity_lookup")
    engine._set_trace_value("route_source", "api_route_hint_override")
    engine._set_trace_value("route_confidence", 1.0)
    engine._set_trace_value("query_kind", "entity_lookup")
    engine._set_trace_value("policy_reason", "api_route_hint_override")
    engine._set_trace_value("route_decision", route_decision)
    engine._set_trace_value("route_boundary_summary", route_boundary_summary)
    engine._set_trace_value("verification_state", "pending")

    raw_entity_answer = await engine._run_entity_lookup(query)
    if not raw_entity_answer:
        return {}
    return await engine._finalize(
        state=None,
        classification=classification,
        raw_result=raw_entity_answer,
        goal_override=query,
        user_id=user_id,
    )


_STREAM_DEEP_RESEARCH_HINT = re.compile(
    r"\b("
    r"latest|current|today|live|breaking|news|update|official\s+statement|"
    r"government\s+policy|company\s+announcement|press\s+release|policy\s+statement"
    r")\b",
    re.I,
)
_STREAM_PROFILE_HINT = re.compile(
    r"\b("
    r"who\s+is|who\s+was|ceo|founder|linkedin|profile|biography|bio|leadership|board\s+member|"
    r"chairman|chairperson|director|coo|cto|cfo"
    r")\b",
    re.I,
)
_STREAM_DOC_HINT = re.compile(
    r"\b(pdf|document|doc|notes|uploaded|upload|file|from this|important questions|"
    r"(?:1|2|5|10|16)\s*mark)\b",
    re.I,
)
_RESEARCH_ROUTE_LABELS = {"deep_research", "deep_search", "news_search", "official_search", "comparison_search"}


def _normalize_progress_route(route_label: str) -> str:
    route = str(route_label or "").strip().lower()
    if route in {"task", "standard_answer", "standard_fsm_task"}:
        return "standard_task"
    return route or "standard_task"


def _infer_stream_route_hint(query: str, doc_context_active: bool) -> str:
    q = str(query or "").strip().lower()
    if doc_context_active and q in {"next", "continue", "go on", "go ahead", "explain this", "summarize this"}:
        return "doc_mode"
    if _STREAM_DOC_HINT.search(q):
        return "doc_mode"
    if _STREAM_PROFILE_HINT.search(q):
        return "entity_lookup"
    if _STREAM_DEEP_RESEARCH_HINT.search(q):
        return "deep_research"
    return "standard_task"


def _progress_label_for_route(route_label: str) -> str:
    route = _normalize_progress_route(route_label)
    if route in {"deep_research", "deep_search"}:
        return "Searching sources..."
    if route == "news_search":
        return "Searching sources..."
    if route == "official_search":
        return "Finding official sources..."
    if route == "comparison_search":
        return "Searching both sides..."
    if route == "entity_lookup":
        return "Checking company sources..."
    if route == "fast_search":
        return "Checking the best live source..."
    if route == "no_search":
        return "Answering directly..."
    if route == "doc_mode":
        return "Reading your document context..."
    if route == "clarification":
        return "Clarifying your request..."
    if route == "fast_message":
        return "Quick reply..."
    return "Drafting answer..."


def _query_topic_snippet(query: str, max_words: int = 6) -> str:
    text = re.sub(r"\s+", " ", str(query or "").strip())
    if not text:
        return "your request"
    words = text.split(" ")
    return " ".join(words[:max_words]).strip() or "your request"


def _preliminary_line_for_route(query: str, route_label: str) -> str:
    topic = _query_topic_snippet(query)
    route = _normalize_progress_route(route_label)
    if route in {"deep_research", "deep_search"}:
        return f"Quick take: I am gathering current evidence for {topic}."
    if route == "news_search":
        return f"Quick take: I am gathering the latest verified updates for {topic}."
    if route == "official_search":
        return f"Quick take: I am checking official sources for {topic}."
    if route == "comparison_search":
        return f"Quick take: I am comparing the strongest evidence for {topic}."
    if route == "entity_lookup":
        return f"Quick take: I am verifying company-role evidence for {topic}."
    if route == "fast_search":
        return f"Quick check: I am verifying the latest answer for {topic}."
    if route == "no_search":
        return f"Quick answer: I am answering directly for {topic}."
    if route == "doc_mode":
        return f"Quick take: I am reading your document context for {topic}."
    if route == "clarification":
        return f"Quick check: I am narrowing the request for {topic}."
    if route == "fast_message":
        return "Quick reply: I am on it."
    return "Quick draft: I am preparing your answer."


@router.post(
    "/execute",
    response_model=AgentResponse,
    summary="Execute an agent query",
    description="Send a developer query and get a structured answer.",
)
async def execute_agent_query(request: AgentRequest, raw_request: Request) -> AgentResponse:
    """
    Primary endpoint — handles all developer queries.

    Flow:
    1. Semantic classification (intent + domain)
    2. Fast path check (instant for simple queries)
    3. Full pipeline if needed (plan → execute → reflect)
    4. Response formatting (answer-first)
    5. Self-evaluation (clarity/correctness/completeness)
    """
    request_id = raw_request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    settings = get_settings()
    logger = TAOSLogger(name="taos.agent", request_id=request_id)
    start_time = time.time()
    request_budget = RequestBudgetManager(_request_timeout_seconds(request.query, settings)).create_budget()
    stage_budget = StageBudgetManager()
    user_id = resolve_user_id(raw_request, request.user_id)
    include_trace_requested = await _include_trace_requested(raw_request, request.include_trace)
    resolved_doc_ids = await _resolve_doc_ids_for_request(
        user_id=user_id,
        chat_id=request.chat_id,
        request_doc_ids=list(request.doc_ids or []),
    )
    resolved_doc_context_active = bool(request.doc_context_active or resolved_doc_ids)
    register_tracker_owner(request_id, user_id)

    try:
        prompt_guard = validate_user_prompt(request.query)
        if not prompt_guard.allowed:
            raise_api_error(400, prompt_guard.code.upper(), prompt_guard.reason, request_id)

        route_hint, owner_hint = _quota_route_owner_hint(
            request.query,
            doc_context_active=resolved_doc_context_active,
        )
        quota_decision = get_quota_manager().check_and_consume_detailed(
            user_id=user_id,
            tier=request.user_tier,
            route=route_hint,
            owner=owner_hint,
            perf_test_mode_requested=_perf_test_mode_requested(raw_request, settings),
        )
        if not quota_decision.allowed:
            raise_api_error(
                429,
                quota_decision.code or "RATE_LIMITED",
                quota_decision.reason or "Rate limit exceeded",
                request_id,
                route=quota_decision.route or route_hint,
                owner=quota_decision.owner or owner_hint,
                retry_after_seconds=quota_decision.retry_after_seconds,
                extra={
                    "limit_per_minute": quota_decision.limit_per_minute,
                    "daily_quota": quota_decision.daily_quota,
                    "effective_tier": quota_decision.effective_tier,
                    "perf_mode_applied": quota_decision.perf_mode_applied,
                },
            )

        if _AMBIGUITY_FALLBACK.should_clarify(request.query, has_context=resolved_doc_context_active):
            clarification_payload = _AMBIGUITY_FALLBACK.build(request_id=request_id, query=request.query)
            clarification_payload = _enrich_frontend_payload(clarification_payload)
            response = _build_agent_response_from_payload(
                clarification_payload,
                request_id=request_id,
                include_trace=include_trace_requested,
            )
            return JSONResponse(content=_agent_response_payload(response))

        micro_answer = _MICRO_FAST_ENGINE.micro_fast_response(request.query)
        if micro_answer:
            elapsed_ms = (time.time() - start_time) * 1000
            micro_payload = _build_micro_fast_payload(
                answer=micro_answer,
                request_id=request_id,
                doc_context_active=resolved_doc_context_active,
                elapsed_ms=elapsed_ms,
                include_trace=include_trace_requested,
            )
            micro_payload = _enrich_frontend_payload(micro_payload)
            response = _build_agent_response_from_payload(
                micro_payload,
                request_id=request_id,
                include_trace=include_trace_requested,
            )
            return JSONResponse(content=_agent_response_payload(response))

        engine = OrchestrationEngine(logger=logger)
        _configure_engine_for_route(engine, route_hint)
        run_kwargs: Dict[str, Any] = {}
        try:
            if route_hint == "entity_lookup":
                raw_result = await asyncio.wait_for(
                    _run_entity_lookup_api_handoff(
                        engine=engine,
                        query=request.query,
                        request_id=request_id,
                        user_id=user_id,
                        include_trace=include_trace_requested,
                        doc_context_active=resolved_doc_context_active,
                    ),
                    timeout=stage_budget.allocate(request_budget),
                )
            else:
                run_kwargs: Dict[str, Any] = {
                    "goal": request.query,
                    "request_id": request_id,
                    "user_id": user_id,
                    "chat_id": request.chat_id,
                    "doc_context_active": resolved_doc_context_active,
                    "doc_ids": resolved_doc_ids,
                    "route_hint_override": route_hint,
                }
                if include_trace_requested:
                    run_kwargs["include_trace"] = True
                raw_result = await asyncio.wait_for(
                    engine.run(**run_kwargs),
                    timeout=stage_budget.allocate(request_budget),
                )
        except TypeError as exc:
            if include_trace_requested and "include_trace" in str(exc):
                run_kwargs.pop("include_trace", None)
                raw_result = await asyncio.wait_for(
                    engine.run(**run_kwargs),
                    timeout=stage_budget.allocate(request_budget),
                )
            else:
                raise
        except TimeoutError:
            partial = get_tracker(request_id).current.partial_result if get_tracker(request_id) else None
            timeout_stage = _tracker_timeout_stage(request_id)
            timeout_payload = _TIMEOUT_FALLBACK.build(
                request_id=request_id,
                elapsed_ms=(time.time() - start_time) * 1000,
                partial_result=partial,
                route="standard_task",
                timeout_stage=timeout_stage,
                budget_stage=timeout_stage,
                partial_answer_used=bool(str(partial or "").strip()),
            )
            timeout_payload = _enrich_frontend_payload(timeout_payload)
            response = _build_agent_response_from_payload(
                timeout_payload,
                request_id=request_id,
                include_trace=include_trace_requested,
            )
            return JSONResponse(content=_agent_response_payload(response))

        elapsed_ms = (time.time() - start_time) * 1000

        # Build simplified response per Backend Implementation Report
        answer = _resolved_answer_text(raw_result)
        if not answer and raw_result.get("error"):
            answer = f"I couldn't complete this query: {raw_result['error']}"
        normalized_payload = dict(raw_result or {})
        if answer:
            normalized_payload["answer"] = str(answer)
        normalized_payload["elapsed_ms"] = round(elapsed_ms, 2)
        normalized_payload["cost"] = raw_result.get("total_cost", raw_result.get("cost", 0.0))
        normalized_payload = _enrich_frontend_payload(normalized_payload)
        response = _build_agent_response_from_payload(
            normalized_payload,
            request_id=request_id,
            include_trace=include_trace_requested,
        )
        return JSONResponse(content=_agent_response_payload(response))

    except HTTPException:
        raise
    except Exception as e:
        logger.error("agent.execute_error", error=redact_secret_text(e))
        raise_api_error(500, "EXECUTION_ERROR", "Execution failed", request_id)


@router.post(
    "/execute/stream",
    summary="Execute query with streaming progress updates",
)
async def execute_agent_query_stream(request: AgentRequest, raw_request: Request):
    request_id = raw_request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    settings = get_settings()
    logger = TAOSLogger(name="taos.agent.stream", request_id=request_id)
    request_budget = RequestBudgetManager(_request_timeout_seconds(request.query, settings)).create_budget()
    stage_budget = StageBudgetManager()
    user_id = resolve_user_id(raw_request, request.user_id)
    include_trace_requested = await _include_trace_requested(raw_request, request.include_trace)
    resolved_doc_ids = await _resolve_doc_ids_for_request(
        user_id=user_id,
        chat_id=request.chat_id,
        request_doc_ids=list(request.doc_ids or []),
    )
    resolved_doc_context_active = bool(request.doc_context_active or resolved_doc_ids)
    register_tracker_owner(request_id, user_id)

    prompt_guard = validate_user_prompt(request.query)
    if not prompt_guard.allowed:
        raise_api_error(400, prompt_guard.code.upper(), prompt_guard.reason, request_id)

    route_hint, owner_hint = _quota_route_owner_hint(
        request.query,
        doc_context_active=resolved_doc_context_active,
    )
    quota_decision = get_quota_manager().check_and_consume_detailed(
        user_id=user_id,
        tier=request.user_tier,
        route=route_hint,
        owner=owner_hint,
        perf_test_mode_requested=_perf_test_mode_requested(raw_request, settings),
    )
    if not quota_decision.allowed:
        raise_api_error(
            429,
            quota_decision.code or "RATE_LIMITED",
            quota_decision.reason or "Rate limit exceeded",
            request_id,
            route=quota_decision.route or route_hint,
            owner=quota_decision.owner or owner_hint,
            retry_after_seconds=quota_decision.retry_after_seconds,
            extra={
                "limit_per_minute": quota_decision.limit_per_minute,
                "daily_quota": quota_decision.daily_quota,
                "effective_tier": quota_decision.effective_tier,
                "perf_mode_applied": quota_decision.perf_mode_applied,
            },
        )

    if _AMBIGUITY_FALLBACK.should_clarify(request.query, has_context=resolved_doc_context_active):
        final_payload = _AMBIGUITY_FALLBACK.build(request_id=request_id, query=request.query)
        final_payload = _enrich_frontend_payload(final_payload)

        async def clarification_event_stream():
            start_event = StreamEvent(
                request_id=request_id,
                event_type=StreamEventType.START,
                phase_name="received",
                progress=0,
                message="Query received",
            )
            yield f"event: {start_event.event_type.value}\ndata: {json.dumps(start_event.to_sse_payload())}\n\n"

            final_event = StreamEvent(
                request_id=request_id,
                event_type=StreamEventType.FINAL,
                phase_name="complete",
                progress=100,
                message="Clarification needed",
                payload=final_payload,
            )
            yield f"event: {final_event.event_type.value}\ndata: {json.dumps(final_event.to_sse_payload())}\n\n"

        return StreamingResponse(
            clarification_event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    micro_started_at = time.time()
    micro_answer = _MICRO_FAST_ENGINE.micro_fast_response(request.query)
    if micro_answer:
        elapsed_ms = (time.time() - micro_started_at) * 1000
        final_payload = _build_micro_fast_payload(
            answer=micro_answer,
            request_id=request_id,
            doc_context_active=resolved_doc_context_active,
            elapsed_ms=elapsed_ms,
            include_trace=True,
        )
        final_payload = normalize_contract_payload(_enrich_frontend_payload(final_payload))

        async def micro_event_stream():
            start_event = StreamEvent(
                request_id=request_id,
                event_type=StreamEventType.START,
                phase_name="received",
                progress=0,
                message="Query received",
            )
            yield f"event: {start_event.event_type.value}\ndata: {json.dumps(start_event.to_sse_payload())}\n\n"

            step_event = StreamEvent(
                request_id=request_id,
                event_type=StreamEventType.STEP_EXECUTED,
                phase_name="routing",
                progress=85,
                message="Fast message route",
                partial_result=micro_answer,
                payload={
                    "route_label": "fast_message",
                    "route_source": "heuristic",
                    "route_confidence": 1.0,
                },
            )
            yield f"event: {step_event.event_type.value}\ndata: {json.dumps(step_event.to_sse_payload())}\n\n"

            final_event = StreamEvent(
                request_id=request_id,
                event_type=StreamEventType.FINAL,
                phase_name="complete",
                progress=100,
                message="Execution complete",
                payload=final_payload,
            )
            yield f"event: {final_event.event_type.value}\ndata: {json.dumps(final_event.to_sse_payload())}\n\n"

        return StreamingResponse(
            micro_event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    engine = OrchestrationEngine(logger=logger)
    _configure_engine_for_route(engine, route_hint)
    final_result_holder: dict = {}

    async def runner():
        run_kwargs: Dict[str, Any] = {}
        try:
            if route_hint == "entity_lookup":
                final_result_holder["result"] = await asyncio.wait_for(
                    _run_entity_lookup_api_handoff(
                        engine=engine,
                        query=request.query,
                        request_id=request_id,
                        user_id=user_id,
                        include_trace=include_trace_requested,
                        doc_context_active=resolved_doc_context_active,
                    ),
                    timeout=stage_budget.allocate(request_budget),
                )
            else:
                run_kwargs: Dict[str, Any] = {
                    "goal": request.query,
                    "request_id": request_id,
                    "user_id": user_id,
                    "chat_id": request.chat_id,
                    "doc_context_active": resolved_doc_context_active,
                    "doc_ids": resolved_doc_ids,
                    "route_hint_override": route_hint,
                }
                if include_trace_requested:
                    run_kwargs["include_trace"] = True
                final_result_holder["result"] = await asyncio.wait_for(
                    engine.run(**run_kwargs),
                    timeout=stage_budget.allocate(request_budget),
                )
        except TypeError as exc:
            if include_trace_requested and "include_trace" in str(exc):
                run_kwargs.pop("include_trace", None)
                final_result_holder["result"] = await asyncio.wait_for(
                    engine.run(**run_kwargs),
                    timeout=stage_budget.allocate(request_budget),
                )
            else:
                final_result_holder["error"] = str(exc)
        except TimeoutError:
            tracker = get_tracker(request_id)
            final_result_holder["error"] = "TIME_BUDGET_EXCEEDED"
            final_result_holder["partial"] = tracker.current.partial_result if tracker else None
        except Exception as exc:
            final_result_holder["error"] = str(exc)

    task = asyncio.create_task(runner())

    stream_started_at = time.time()

    async def event_stream():
        first_visible_sent_at: Optional[float] = None

        def _mark_first_visible() -> None:
            nonlocal first_visible_sent_at
            if first_visible_sent_at is None:
                first_visible_sent_at = time.time()

        try:
            start_event = StreamEvent(
                request_id=request_id,
                event_type=StreamEventType.START,
                phase_name="received",
                progress=0,
                message="Query received",
            )
            yield f"event: {start_event.event_type.value}\ndata: {json.dumps(start_event.to_sse_payload())}\n\n"
            route_hint = _infer_stream_route_hint(
                request.query,
                resolved_doc_context_active,
            )
            route_event = StreamEvent(
                request_id=request_id,
                event_type=StreamEventType.PROGRESS,
                phase_name="routing",
                progress=2,
                message=_progress_label_for_route(route_hint),
                partial_result=_preliminary_line_for_route(request.query, route_hint),
                payload={
                    "route_label": route_hint,
                    "route_source": "heuristic_hint",
                    "route_confidence": 0.6,
                    "pipeline_stage": "routing",
                    "preliminary": True,
                },
            )
            yield f"event: {route_event.event_type.value}\ndata: {json.dumps(route_event.to_sse_payload())}\n\n"
            _mark_first_visible()

            last_len = 0
            last_ping = time.time()
            while not task.done():
                tracker = get_tracker(request_id)
                if tracker:
                    history = tracker.history
                    if len(history) > last_len:
                        for item in history[last_len:]:
                            event_type = phase_to_event_type(
                                str(item.get("phase", "")),
                                str(item.get("detail", "")),
                            )
                            if item.get("partial_result"):
                                event_type = StreamEventType.TOKEN
                            elif event_type == StreamEventType.STEP_EXECUTED:
                                event_type = StreamEventType.PROGRESS
                            stream_event = StreamEvent(
                                request_id=request_id,
                                event_type=event_type,
                                phase_name=str(item.get("phase", "")),
                                progress=int(item.get("progress", 0) or 0),
                                message=str(item.get("label", "")),
                                partial_result=item.get("partial_result"),
                                payload=item,
                            )
                            yield (
                                f"event: {stream_event.event_type.value}\n"
                                f"data: {json.dumps(stream_event.to_sse_payload())}\n\n"
                            )
                            if stream_event.partial_result:
                                _mark_first_visible()
                        last_len = len(history)
                if (time.time() - last_ping) >= float(settings.sse_heartbeat_seconds):
                    ping_event = StreamEvent(
                        request_id=request_id,
                        event_type=StreamEventType.PING,
                        phase_name="heartbeat",
                        progress=0,
                        message="ping",
                    )
                    yield f"event: {ping_event.event_type.value}\ndata: {json.dumps(ping_event.to_sse_payload())}\n\n"
                    last_ping = time.time()
                await asyncio.sleep(0.05)

            await task
            if "error" in final_result_holder:
                error_code = str(final_result_holder.get("error") or "EXECUTION_ERROR")
                timeout_stage = _tracker_timeout_stage(request_id)
                timeout_payload = _TIMEOUT_FALLBACK.build(
                    request_id=request_id,
                    elapsed_ms=(time.time() - stream_started_at) * 1000,
                    partial_result=final_result_holder.get("partial"),
                    route=route_hint,
                    owner=_ROUTE_DISPATCHER.owner_for_route(route_hint),
                    timeout_stage=timeout_stage,
                    budget_stage=timeout_stage,
                    partial_answer_used=bool(str(final_result_holder.get("partial") or "").strip()),
                    first_event_latency_ms=_first_event_latency_ms(stream_started_at, first_visible_sent_at),
                    streaming_started_at=stream_started_at,
                )
                if error_code != "TIME_BUDGET_EXCEEDED":
                    timeout_payload["error"] = error_code
                    timeout_payload["warnings"] = ["The request failed and returned a safe fallback response."]
                    timeout_payload["metadata"] = {
                        **dict(timeout_payload.get("metadata") or {}),
                        "fallback_reason": "stream_error",
                    }
                timeout_payload = _attach_stream_runtime_trace(
                    timeout_payload,
                    stream_started_at=stream_started_at,
                    first_visible_sent_at=first_visible_sent_at,
                    budget_exceeded=True,
                    timeout_stage=timeout_stage,
                    partial_answer_used=bool(str(final_result_holder.get("partial") or "").strip()),
                )
                timeout_payload = _enrich_frontend_payload(timeout_payload)
                error_event = StreamEvent(
                    request_id=request_id,
                    event_type=StreamEventType.FINAL,
                    phase_name="complete",
                    progress=100,
                    message="Returned fallback response",
                    payload=timeout_payload,
                )
                yield f"event: {error_event.event_type.value}\ndata: {json.dumps(error_event.to_sse_payload())}\n\n"
                return

            result = final_result_holder.get("result", {})
            final_payload = dict(result or {})
            answer_text = _resolved_answer_text(final_payload)
            if answer_text:
                final_payload["answer"] = answer_text
                final_payload["result"] = answer_text
            final_payload = normalize_contract_payload(_enrich_frontend_payload(final_payload))
            time_to_final_ms = max(0.0, (time.time() - stream_started_at) * 1000)
            if first_visible_sent_at is None:
                time_to_first_token_ms = time_to_final_ms
            else:
                time_to_first_token_ms = max(
                    0.0,
                    (first_visible_sent_at - stream_started_at) * 1000,
                )
            trace_blob = final_payload.get("trace")
            if not isinstance(trace_blob, dict):
                trace_blob = {}
                final_payload["trace"] = trace_blob
            timing = trace_blob.setdefault("timing", {})
            if not isinstance(timing, dict):
                timing = {}
                trace_blob["timing"] = timing
            timing["time_to_first_token_ms"] = round(time_to_first_token_ms, 2)
            timing["time_to_final_ms"] = round(time_to_final_ms, 2)
            final_payload = _attach_stream_runtime_trace(
                final_payload,
                stream_started_at=stream_started_at,
                first_visible_sent_at=first_visible_sent_at,
                budget_exceeded=bool(trace_blob.get("budget_exceeded")),
                timeout_stage=str(trace_blob.get("timeout_stage") or ""),
                partial_answer_used=bool(first_visible_sent_at is not None),
            )
            final_event = StreamEvent(
                request_id=request_id,
                event_type=StreamEventType.FINAL,
                phase_name="complete",
                progress=100,
                message="Execution complete",
                payload=final_payload,
            )
            yield f"event: {final_event.event_type.value}\ndata: {json.dumps(final_event.to_sse_payload())}\n\n"
        except asyncio.CancelledError:
            logger.info("sse_client_disconnected", request_id=request_id)
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            raise
        except Exception as exc:
            logger.exception("sse_stream_error", error=str(exc), request_id=request_id)
            # Best-effort terminal error event for clients still connected.
            err_event = StreamEvent(
                request_id=request_id,
                event_type=StreamEventType.ERROR,
                phase_name="failed",
                progress=100,
                message="Stream interrupted",
                payload={
                    "error_code": "STREAM_INTERRUPTED",
                    "message": "Stream interrupted",
                    "request_id": request_id,
                },
            )
            yield f"event: {err_event.event_type.value}\ndata: {json.dumps(err_event.to_sse_payload())}\n\n"
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
