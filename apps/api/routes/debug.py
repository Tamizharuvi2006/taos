"""
TAOS API — Debug/trace endpoints for development.

These endpoints are only available in development mode.
"""

from __future__ import annotations

import contextlib
import traceback
from types import SimpleNamespace
from typing import Any, Dict, Optional
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from taos.apps.api.auth_context import resolve_user_id
from taos.config.settings import get_settings
from taos.core.fast_path.query_cache import QueryCache
from taos.core.notifications.models import NotificationChannel, NotificationConfig, NotificationEvent
from taos.core.notifications.notifier import NotificationManager
from taos.core.persistence.firestore_memory import FirestoreMemorySchema
from taos.core.routing import RouteDecider, normalize_query
from taos.infra.firebase import get_firestore_client, init_firebase_admin
from taos.orchestration.engine import OrchestrationEngine

router = APIRouter(tags=["debug"], prefix="/debug")


class DebugRouteRequest(BaseModel):
    query: str = Field(..., description="User query to route without executing it")
    has_active_doc: bool = Field(default=False, description="Whether an uploaded document is active")


def _debug_route_owner(route: str) -> str:
    route = str(route or "").strip().lower()
    if route == "fast_message":
        return "direct_fast_message"
    if route == "no_search":
        return "direct_llm_no_tools"
    if route == "fast_search":
        return "search_lite"
    if route in {"deep_search", "news_search", "official_search", "comparison_search"}:
        return "research_pipeline"
    if route == "doc_mode":
        return "document_pipeline"
    if route == "task":
        return "fsm_planner"
    if route == "clarification":
        return "clarification_fallback"
    return "direct_standard"


@router.post("/route")
async def route_debug(request: DebugRouteRequest) -> Dict[str, Any]:
    """Return the deterministic route decision without executing search, tools, or planner."""
    settings = get_settings()
    if settings.is_production:
        raise HTTPException(status_code=403, detail="Debug endpoints disabled in production")

    query = str(request.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    context = {"has_active_doc": bool(request.has_active_doc)}
    decision = await RouteDecider().decide(query, context=context)
    route = str(decision.route or "").strip().lower()
    owner = _debug_route_owner(route)
    return {
        "query": query,
        "normalized_query": normalize_query(query),
        "route": route,
        "route_owner": owner,
        "confidence": round(float(decision.confidence or 0.0), 3),
        "reason": str(decision.reason or ""),
        "matched_rules": list(decision.matched_rules or []),
        "boundary": str(decision.boundary or ""),
        "cache_status": str(decision.cache_status or ""),
        "llm_fallback_used": bool(decision.used_llm),
        "high_stakes": bool(decision.high_stakes),
        "will_use_web": route in {"fast_search", "deep_search", "news_search", "official_search", "comparison_search"},
        "will_use_planner": route == "task",
        "will_use_research_pipeline": owner == "research_pipeline",
        "will_use_search_lite": owner == "search_lite",
        "will_use_document_pipeline": owner == "document_pipeline",
    }


@router.get("/config")
async def get_config():
    """Return current configuration (non-sensitive fields only)."""
    settings = get_settings()
    if settings.is_production:
        raise HTTPException(status_code=403, detail="Debug endpoints disabled in production")

    return {
        "environment": settings.taos_env,
        "planner_model": settings.planner_model,
        "executor_model": settings.executor_model,
        "reflection_model": settings.reflection_model,
        "fallback_model": settings.fallback_model,
        "max_task_time": settings.max_task_time,
        "max_step_time": settings.max_step_time,
        "max_steps": settings.max_steps,
        "max_retries": settings.max_retries,
        "max_replans": settings.max_replans,
        "cost_budget_per_task": settings.cost_budget_per_task,
        "confidence_retry_threshold": settings.confidence_retry_threshold,
        "confidence_terminate_threshold": settings.confidence_terminate_threshold,
        "log_level": settings.log_level,
        "has_openrouter_key": bool(settings.openrouter_api_key),
        "has_serper_key": bool(settings.serper_api_key),
    }


@router.get("/tools")
async def list_tools():
    """List all registered tools and their policies."""
    settings = get_settings()
    if settings.is_production:
        raise HTTPException(status_code=403, detail="Debug endpoints disabled in production")

    from taos.core.tools.registry import ToolRegistry
    from taos.core.tools.builtin import register_all_builtin_tools

    registry = ToolRegistry()
    register_all_builtin_tools(registry)

    tools = []
    for name in registry.list_names():
        definition = registry.get(name)
        if definition:
            tools.append({
                "name": definition.name,
                "description": definition.description,
                "risk_level": definition.policy.risk_level.value if definition.policy else "unknown",
                "rate_limit": definition.rate_limit,
                "audit_required": definition.policy.audit_required if definition.policy else False,
            })

    return {"tools": tools, "count": len(tools)}


@router.get("/fsm-states")
async def list_fsm_states():
    """List all FSM states and valid transitions."""
    settings = get_settings()
    if settings.is_production:
        raise HTTPException(status_code=403, detail="Debug endpoints disabled in production")

    from taos.config.constants import FSMState
    from taos.core.controller.transitions import TransitionEngine

    engine = TransitionEngine()

    states = {}
    for state in FSMState:
        transitions = []
        for target in FSMState:
            # Build a minimal mock state to check transitions
            if engine._transitions.get((state.value, target.value)):
                t = engine._transitions[(state.value, target.value)]
                transitions.append({
                    "to": target.value,
                    "description": t.description,
                    "has_guard": t.guard is not None,
                })
        states[state.value] = {
            "transitions": transitions,
            "is_terminal": state in {FSMState.TERMINATED, FSMState.FAILED},
        }

    return {"states": states}


@router.get("/firebase")
async def firebase_debug():
    """Deep Firebase/Firestore diagnostics for local debugging."""
    settings = get_settings()
    if settings.is_production:
        raise HTTPException(status_code=403, detail="Debug endpoints disabled in production")

    out = {
        "configured": {
            "project_id": settings.firebase_project_id,
            "database_id": settings.firebase_database_id,
            "has_client_email": bool(settings.firebase_client_email),
            "has_private_key": bool(settings.firebase_private_key),
            "storage_backend": settings.storage_backend,
        },
        "admin_init": {"ok": False, "error": ""},
        "candidates": [],
        "resolved_client": {"ok": False, "type": "", "database": "", "error": ""},
    }

    try:
        init_firebase_admin()
        out["admin_init"]["ok"] = True
    except Exception as exc:
        out["admin_init"]["error"] = str(exc)

    project_id = (settings.firebase_project_id or "").strip() or None
    requested_db = (settings.firebase_database_id or "").strip()
    candidates = []
    if requested_db and requested_db != "(default)":
        candidates = [requested_db]
    else:
        if requested_db:
            candidates.append(requested_db)
        if "(default)" not in candidates:
            candidates.append("(default)")
        if "relyceinfotech" not in candidates:
            candidates.append("relyceinfotech")

    try:
        import firebase_admin
        from firebase_admin import firestore
        from google.cloud import firestore as google_firestore

        app = firebase_admin.get_app()
        cred = None
        try:
            cred = app.credential.get_credential()
        except Exception:
            cred = None

        for database_id in candidates:
            result = {"database_id": database_id, "ok": False, "error": ""}
            try:
                if database_id == "(default)":
                    client = firestore.client()
                else:
                    if cred is not None:
                        client = google_firestore.Client(
                            credentials=cred,
                            project=project_id,
                            database=database_id,
                        )
                    else:
                        client = google_firestore.Client(project=project_id, database=database_id)
                _ = list(
                    client.collection("taos_health")
                    .document("probe")
                    .collection("ping")
                    .limit(1)
                    .stream()
                )
                result["ok"] = True
            except Exception as exc:
                result["error"] = str(exc)
            out["candidates"].append(result)
    except Exception as exc:
        out["candidates"].append(
            {
                "database_id": "probe_setup",
                "ok": False,
                "error": f"{exc}\n{traceback.format_exc(limit=1)}",
            }
        )

    try:
        client = get_firestore_client()
        if client is not None:
            out["resolved_client"]["ok"] = True
            out["resolved_client"]["type"] = str(type(client))
            db_name = ""
            try:
                db_name = str(getattr(client, "_database_string", "") or getattr(client, "_database", ""))
            except Exception:
                db_name = ""
            out["resolved_client"]["database"] = db_name
        else:
            out["resolved_client"]["error"] = "get_firestore_client returned None"
    except Exception as exc:
        out["resolved_client"]["error"] = str(exc)

    return out


@router.get("/research-trace")
async def research_trace_debug(request_id: Optional[str] = None, limit: int = 120):
    """Return recent TAOS research-stage trace events for debugging."""
    settings = get_settings()
    if settings.is_production:
        raise HTTPException(status_code=403, detail="Debug endpoints disabled in production")

    safe_limit = max(1, min(int(limit), 400))
    rows = OrchestrationEngine.get_recent_research_trace(
        request_id=request_id,
        limit=safe_limit,
    )
    return {
        "count": len(rows),
        "limit": safe_limit,
        "request_id": request_id,
        "traces": rows,
    }


@router.get("/research-cache")
async def research_cache_debug(
    raw_request: Request,
    query: str = "",
    user_id: Optional[str] = None,
    limit: int = 5,
    min_similarity: Optional[float] = None,
):
    """
    Inspect semantic research cache hits/misses.

    - Requires auth in development (supports AUTH_ALLOW_DEV_BYPASS).
    - Use `query` to probe live semantic retrieval.
    """
    settings = get_settings()
    if settings.is_production:
        raise HTTPException(status_code=403, detail="Debug endpoints disabled in production")

    scoped_user_id = resolve_user_id(raw_request, user_id)
    safe_limit = max(1, min(int(limit), 20))
    svc = FirestoreMemorySchema()

    if not str(query or "").strip():
        rows = await svc.store.list(
            "research_profiles",
            user_id=scoped_user_id,
            limit=safe_limit,
        )
        rows = sorted(rows, key=lambda r: float((r or {}).get("created_at") or 0.0), reverse=True)
        preview = []
        for row in rows[:safe_limit]:
            preview.append(
                {
                    "id": row.get("id"),
                    "query": row.get("query"),
                    "created_at": row.get("created_at"),
                    "expires_at": row.get("expires_at"),
                    "related_questions": list((row.get("related_questions") or [])[:4]),
                    "source_count": len(list(row.get("source_rows") or [])),
                }
            )
        return {
            "user_id": scoped_user_id,
            "query": "",
            "hit": False,
            "count": len(preview),
            "rows": preview,
            "note": "Pass ?query=... to compute semantic hit/miss and scores.",
        }

    hits = await svc.retrieve_research_profiles(
        user_id=scoped_user_id,
        query=query,
        limit=safe_limit,
        min_similarity=min_similarity,
    )
    rows = []
    for row in hits:
        rows.append(
            {
                "id": row.get("id"),
                "query": row.get("query"),
                "retrieval_score": row.get("retrieval_score"),
                "created_at": row.get("created_at"),
                "expires_at": row.get("expires_at"),
                "related_questions": list((row.get("related_questions") or [])[:4]),
                "agreement": row.get("agreement"),
                "source_count": len(list(row.get("source_rows") or [])),
            }
        )
    return {
        "user_id": scoped_user_id,
        "query": str(query).strip(),
        "hit": bool(rows),
        "count": len(rows),
        "rows": rows,
        "min_similarity": (
            float(min_similarity)
            if min_similarity is not None
            else float(getattr(settings, "research_cache_min_similarity", 0.58))
        ),
    }


@router.get("/research-extractors")
async def research_extractors_debug(
    raw_request: Request,
    query: str,
    user_id: Optional[str] = None,
    doc_context_active: bool = False,
):
    """
    Run one live request and return per-candidate extractor diagnostics.

    Useful to confirm:
    - selected extractor adapter,
    - final adapter used,
    - fallback reasons per URL.
    """
    settings = get_settings()
    if settings.is_production:
        raise HTTPException(status_code=403, detail="Debug endpoints disabled in production")

    goal = str(query or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="query is required")

    scoped_user_id = resolve_user_id(raw_request, user_id)
    engine = OrchestrationEngine()
    with contextlib.suppress(Exception):
        engine._latency.cache.clear()  # type: ignore[attr-defined]
    with contextlib.suppress(Exception):
        QueryCache._global_cache.clear()
    async def _no_research_profile_cache(*args, **kwargs):
        return None
    with contextlib.suppress(Exception):
        engine._lookup_research_profile_cache = _no_research_profile_cache  # type: ignore[assignment]

    request_id = f"debug_extract_{uuid4().hex[:10]}"
    with contextlib.suppress(Exception):
        engine._settings.entity_lookup_v1_enabled = True
    with contextlib.suppress(Exception):
        engine._settings.scrapling_http_extractor_enabled = True
    with contextlib.suppress(Exception):
        engine._reset_execution_trace(
            request_id=request_id,
            goal=goal,
            include_trace=True,
        )
    answer = await engine._run_entity_lookup(goal)
    trace_dict: Dict[str, Any] = dict(getattr(engine, "_trace_data", {}) or {})
    extractor_rows = list(trace_dict.get("extractor_candidates") or [])
    evidence_stats = dict(trace_dict.get("evidence_stats") or {})

    return {
        "query": goal,
        "request_id": trace_dict.get("request_id") or request_id,
        "route_label": trace_dict.get("route_label") or "deep_research",
        "planner_path": trace_dict.get("planner_path"),
        "query_kind": trace_dict.get("query_kind"),
        "verification_state": trace_dict.get("verification_state"),
        "policy_reason": trace_dict.get("policy_reason"),
        "answer_preview": str(answer or "")[:400],
        "extractor_adapters": trace_dict.get("extractor_adapters") or {},
        "extractor_adapter_fallback_reasons": trace_dict.get("extractor_adapter_fallback_reasons") or [],
        "extractor_candidates": extractor_rows,
        "candidate_count": len(extractor_rows),
        "evidence_source_count": int(evidence_stats.get("source_count") or 0),
        "extract_count": int(evidence_stats.get("extract_count") or 0),
        "extract_rejected_count": int(evidence_stats.get("extract_rejected_count") or 0),
        "confirmed_count": int(evidence_stats.get("confirmed_count") or 0),
        "partially_confirmed_count": int(evidence_stats.get("partially_confirmed_count") or 0),
        "not_verified_count": int(evidence_stats.get("not_verified_count") or 0),
        "http_attempts": int(evidence_stats.get("http_attempts") or 0),
        "dynamic_attempts": int(evidence_stats.get("dynamic_attempts") or 0),
        "stealth_attempts": int(evidence_stats.get("stealth_attempts") or 0),
        "http_success": int(evidence_stats.get("http_success") or 0),
        "dynamic_success": int(evidence_stats.get("dynamic_success") or 0),
        "stealth_success": int(evidence_stats.get("stealth_success") or 0),
        "fallback_count": int(evidence_stats.get("fallback_count") or 0),
        "skip_count_by_policy": dict(evidence_stats.get("skip_count_by_policy") or {}),
        "domain_policy_hits": int(evidence_stats.get("domain_policy_hits") or 0),
        "usable_role_claim_count": int(evidence_stats.get("usable_role_claim_count") or 0),
    }


class DebugNotifyRequest(BaseModel):
    to_email: str = Field(..., description="Target email address")
    user_id: str = Field(default="default", description="User id for FCM token lookup")
    subject: str = Field(default="Relyce AI - 1 Month Business Plan Free")
    message: str = Field(
        default=(
            "Hi from Relyce AI,\n\n"
            "You are getting Business Plan access FREE for 1 month.\n"
            "This includes advanced chat workflows, reminders, and priority automation features.\n\n"
            "Upgrade is already applied to your account preview environment.\n"
            "If you need help, reply to this email and our team will assist you.\n\n"
            "Thanks,\nRelyce AI Team"
        )
    )
    send_fcm: bool = Field(default=True, description="Also try FCM push for user_id")


@router.post("/notify")
async def debug_notify(request: DebugNotifyRequest) -> Dict[str, Any]:
    """
    Trigger real debug notification send (email + optional FCM) with channel-wise result.
    Development only.
    """
    settings = get_settings()
    if settings.is_production:
        raise HTTPException(status_code=403, detail="Debug endpoints disabled in production")

    manager = NotificationManager()
    task = SimpleNamespace(
        task_id="debug_notify_task",
        name="Debug Notify",
        goal=request.subject,
    )
    execution = SimpleNamespace(
        execution_id="debug_exec",
        success=True,
        result=request.message,
        error="",
    )

    email_config = NotificationConfig(
        channel=NotificationChannel.EMAIL,
        target=request.to_email,
        events=[NotificationEvent.ALWAYS],
    )

    email_ok = await manager._send_email(
        config=email_config,
        task=task,
        execution=execution,
        event=NotificationEvent.ALWAYS,
        user_id=request.user_id,
    )

    fcm_result: Dict[str, Any] = {"requested": request.send_fcm, "ok": False, "reason": "not_requested"}
    if request.send_fcm:
        fcm_ok = await manager._send_fcm(
            user_id=request.user_id,
            task=task,
            execution=execution,
            event=NotificationEvent.ALWAYS,
        )
        fcm_result = {
            "requested": True,
            "ok": bool(fcm_ok),
            "reason": "" if fcm_ok else "no_tokens_or_provider_error",
        }

    return {
        "ok": bool(email_ok) and (fcm_result["ok"] if request.send_fcm else True),
        "email": {
            "to": request.to_email,
            "ok": bool(email_ok),
            "provider_configured": bool(settings.zeptomail_api_key),
            "from_email_configured": bool(settings.zeptomail_from_email),
            "reason": "" if email_ok else "provider_error_or_unavailable",
        },
        "fcm": fcm_result,
    }
