"""
Unified response contract helpers for TAOS API responses.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered


def normalize_contract_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload or {})
    frontend_hints = normalized.get("frontend_hints")
    route = str(
        normalized.get("public_route_label")
        or normalized.get("route")
        or normalized.get("route_label")
        or ""
    ).strip()
    if not route and isinstance(frontend_hints, dict):
        route = str(frontend_hints.get("public_route_label") or frontend_hints.get("route_label") or "").strip()
    if not route:
        route = "standard_task"

    metadata = dict(normalized.get("metadata") or {})
    route_decision = dict(normalized.get("route_decision") or metadata.get("route_decision") or {})
    route_boundary = dict(normalized.get("route_boundary_summary") or metadata.get("route_boundary_summary") or {})
    selected_route = str(
        normalized.get("selected_route")
        or metadata.get("selected_route")
        or route_decision.get("selected_route")
        or route_boundary.get("selected_route")
        or route
        or ""
    ).strip()
    original_route_hint = str(
        normalized.get("original_route_hint")
        or metadata.get("original_route_hint")
        or route_boundary.get("route")
        or route_decision.get("route")
        or route
        or ""
    ).strip()
    public_route_label = str(
        normalized.get("public_route_label")
        or metadata.get("public_route_label")
        or (
            selected_route
            if str(normalized.get("route_label") or "").strip().lower() in {"", "standard_task"}
            else normalized.get("route_label")
        )
        or selected_route
        or route
        or ""
    ).strip()
    route_owner = str(
        normalized.get("route_owner")
        or normalized.get("owner")
        or metadata.get("route_owner")
        or metadata.get("owner")
        or route_boundary.get("owner")
        or route_decision.get("route_owner")
        or ""
    ).strip()

    if selected_route and "selected_route" not in normalized:
        normalized["selected_route"] = selected_route
    if public_route_label and "public_route_label" not in normalized:
        normalized["public_route_label"] = public_route_label
    normalized["route"] = public_route_label or route
    if original_route_hint and "original_route_hint" not in normalized:
        normalized["original_route_hint"] = original_route_hint
    if route_owner:
        normalized.setdefault("route_owner", route_owner)
        normalized.setdefault("owner", route_owner)
    current_route_label = str(normalized.get("route_label") or "").strip().lower()
    if public_route_label and (not current_route_label or current_route_label == "standard_task"):
        normalized["route_label"] = public_route_label
    trace_payload = normalized.get("trace")
    if isinstance(trace_payload, dict):
        trace_payload.setdefault("selected_route", selected_route)
        trace_payload.setdefault("original_route_hint", original_route_hint)
        trace_payload.setdefault("route_owner", route_owner)
        trace_payload.setdefault("owner", route_owner)
        current_trace_route = str(trace_payload.get("public_route_label") or trace_payload.get("route_label") or "").strip().lower()
        if public_route_label and current_trace_route in {"", "standard_task"}:
            trace_payload["public_route_label"] = public_route_label
            trace_payload["route_label"] = public_route_label

    trace_payload = normalized.get("trace") if isinstance(normalized.get("trace"), dict) else {}
    entity_summary = dict(
        normalized.get("entity_intelligence_summary")
        or metadata.get("entity_intelligence_summary")
        or trace_payload.get("entity_intelligence_summary")
        or {}
    )
    evidence_stats = dict(
        normalized.get("evidence_stats")
        or metadata.get("evidence_stats")
        or trace_payload.get("evidence_stats")
        or {}
    )
    if entity_summary:
        normalized["entity_intelligence_summary"] = entity_summary
        metadata.setdefault("entity_intelligence_summary", entity_summary)
        if isinstance(trace_payload, dict):
            trace_payload.setdefault("entity_intelligence_summary", entity_summary)
    trust_block = normalized.get("trust_block")
    if isinstance(trust_block, dict):
        if entity_summary:
            for key in (
                "answer_mode",
                "requested_role",
                "supported_role",
                "selected_candidate",
                "exact_role_verified",
                "role_match",
                "role_mismatch_reason",
                "linkedin_source_found",
                "official_source_found",
                "registry_source_found",
                "search_lanes_used",
                "source_tiers_found",
                "confidence_reason",
                "conflict_detected",
                "verification_state",
            ):
                if key in entity_summary and trust_block.get(key) is None:
                    trust_block[key] = entity_summary.get(key)
        if evidence_stats:
            for key in (
                "entity_answer_mode",
                "requested_role",
                "supported_role",
                "selected_candidate",
                "exact_role_verified",
                "role_match",
                "role_mismatch_reason",
                "linkedin_source_found",
                "official_source_found",
                "registry_source_found",
                "search_lanes_used",
                "source_tiers_found",
                "confidence_reason",
                "conflict_detected",
                "verification_state",
            ):
                if key in evidence_stats and trust_block.get(key) is None:
                    trust_block[key] = evidence_stats.get(key)

    sections = list(normalized.get("sections") or normalized.get("answer_sections") or [])
    normalized["sections"] = sections
    normalized["answer_sections"] = sections

    uncertainty_box = normalized.get("uncertainty_box") or {}
    warning_messages = []
    if isinstance(uncertainty_box, dict):
        warning_messages.extend(list(uncertainty_box.get("messages") or []))
    if normalized.get("error"):
        warning_messages.append(str(normalized.get("error")))
    normalized["warnings"] = _dedupe_preserve_order(list(normalized.get("warnings") or []) + warning_messages)

    for key in (
        "request_id",
        "replan_count",
        "fast_path",
        "elapsed_ms",
        "cost",
        "query_kind",
        "verification_state",
        "policy_reason",
        "route_source",
        "route_confidence",
        "route_decision",
        "route_boundary_summary",
        "query_frame",
        "query_frame_mismatch_count",
        "query_frame_aligned_count",
        "query_frame_unknown_count",
        "query_frame_supported_multilingual_count",
        "query_frame_semantic_fallback_used_count",
        "query_frame_fastpath_used_count",
        "query_frame_low_confidence_count",
        "query_frame_entity_handoff_enabled",
        "query_frame_entity_handoff_applied",
        "query_frame_entity_handoff_blocked_reason",
        "entity_handoff_source",
        "entity_handoff_lookup_type",
        "entity_handoff_entity_name",
        "entity_handoff_requested_role",
        "entity_handoff_answer_language",
        "answer_language",
        "answer_language_source",
        "language_preservation_applied",
        "language_preservation_status",
        "language_preservation_limitations",
        "entity_search_queries_generated",
        "entity_search_query_lanes",
        "legacy_entity_resolver_used",
        "doc_context_active",
        "evidence_matrix_summary",
        "freshness_summary",
        "evidence_selection_summary",
        "citation_plan_summary",
        "diversity_summary",
        "conflict_summary",
        "high_stakes_summary",
        "cache_summary",
        "document_summary",
        "source_diversity_score",
        "extraction_recovery_used",
        "selected_route",
        "public_route_label",
        "original_route_hint",
        "route_owner",
        "owner",
        "entity_intelligence_summary",
        "entity_answer_mode",
        "entity_answer_mode_source",
        "entity_profile_link_found",
        "entity_official_website_found",
        "entity_business_presence_supported",
        "entity_registry_evidence_found",
        "entity_legal_registration_verified",
        "entity_answer_limitations",
        "selected_candidate",
        "requested_role",
        "supported_role",
        "exact_role_verified",
        "role_match",
        "role_mismatch_reason",
        "linkedin_source_found",
        "official_source_found",
        "registry_source_found",
        "search_lanes_used",
        "source_tiers_found",
        "confidence_reason",
    ):
        value = normalized.get(key)
        if value is not None and key not in metadata:
            metadata[key] = value
    normalized["metadata"] = metadata
    return normalized


def build_timeout_payload(
    *,
    request_id: str,
    elapsed_ms: float,
    partial_result: Optional[str] = None,
    route: str = "standard_task",
    owner: str = "",
    timeout_stage: Optional[str] = None,
    budget_stage: Optional[str] = None,
    partial_answer_used: Optional[bool] = None,
    first_event_latency_ms: Optional[float] = None,
    streaming_started_at: Optional[float] = None,
) -> Dict[str, Any]:
    answer = str(partial_result or "").strip() or (
        "I could not finish within the request time budget. "
        "Here is the safest partial state: the request needs another pass."
    )
    used_partial = bool(partial_answer_used) or bool(str(partial_result or "").strip())
    safe_timeout_stage = str(timeout_stage or budget_stage or "runtime").strip() or "runtime"
    safe_owner = str(owner or "").strip()
    return normalize_contract_payload(
        {
            "answer": answer,
            "direct_answer": answer,
            "key_points": [],
            "intent": "task",
            "domain": "general",
            "mode": "standard",
            "confidence": 0.18,
            "sources": [],
            "fast_path": False,
            "evaluation": None,
            "steps_executed": 0,
            "cost": 0.0,
            "elapsed_ms": round(float(elapsed_ms or 0.0), 2),
            "error": "TIME_BUDGET_EXCEEDED",
            "request_id": request_id,
            "replan_count": 0,
            "trust_block": {
                "freshness": "Unknown",
                "evidence": "Minimal",
                "execution_path": "Fallback",
                "fallback_used": True,
                "confidence": "Low",
                "source_count": 0,
                "usable_sources_count": 0,
                "rejected_sources_count": 0,
                "official_source_count": 0,
                "agreement": "unknown",
                "agreement_score": 0.0,
                "conflict_detected": False,
                "stale_detected": False,
                "signal": "candidate_only",
                "event_agreement": "unknown",
                "attribution_agreement": "unknown",
                "domain_diversity": 0.0,
                "extraction_quality": 0.0,
                "official_source_required": False,
                "official_source_found": False,
                "high_stakes_mode": False,
                "uncertainty_flags": ["timeout_fallback"],
            },
            "route": route,
            "route_label": route,
            "warnings": [
                "The request hit its timeout budget and returned a safe partial fallback.",
            ],
            "metadata": {
                "fallback_reason": "timeout",
                "budget_stage": safe_timeout_stage,
                "timeout_stage": safe_timeout_stage,
                "partial_answer_used": used_partial,
                "contract_version": "2026-04-24",
            },
            "trace": {
                "request_id": request_id,
                "route_label": route,
                "planner_path": safe_owner or route,
                "fallback_used": True,
                "fallback_reason": "timeout",
                "budget_exceeded": True,
                "timeout_stage": safe_timeout_stage,
                "partial_answer_used": used_partial,
                "streaming_started_at": round(float(streaming_started_at or 0.0), 6)
                if streaming_started_at is not None
                else None,
                "first_event_latency_ms": round(float(first_event_latency_ms or 0.0), 2),
                "timing": {
                    "total_ms": round(float(elapsed_ms or 0.0), 2),
                    "time_to_final_ms": round(float(elapsed_ms or 0.0), 2),
                    "first_event_latency_ms": round(float(first_event_latency_ms or 0.0), 2),
                    "streaming_started_at": round(float(streaming_started_at or 0.0), 6)
                    if streaming_started_at is not None
                    else None,
                },
            },
        }
    )


def build_clarification_payload(
    *,
    request_id: str,
    query: str,
    route: str = "standard_task",
) -> Dict[str, Any]:
    answer = (
        "I need one clearer anchor before I continue. "
        "Tell me the exact topic, entity, or previous answer you want me to use."
    )
    return normalize_contract_payload(
        {
            "answer": answer,
            "direct_answer": answer,
            "key_points": [
                "Name the topic or entity.",
                "Or paste the text you want me to continue from.",
            ],
            "intent": "task",
            "domain": "general",
            "mode": "standard",
            "confidence": 0.35,
            "sources": [],
            "fast_path": False,
            "evaluation": None,
            "steps_executed": 0,
            "cost": 0.0,
            "elapsed_ms": 0.0,
            "error": None,
            "request_id": request_id,
            "replan_count": 0,
            "trust_block": {
                "freshness": "Unknown",
                "evidence": "Minimal",
                "execution_path": "Fallback",
                "fallback_used": True,
                "confidence": "Low",
                "source_count": 0,
                "usable_sources_count": 0,
                "rejected_sources_count": 0,
                "official_source_count": 0,
                "agreement": "unknown",
                "agreement_score": 0.0,
                "conflict_detected": False,
                "stale_detected": False,
                "signal": "clarification",
                "official_source_required": False,
                "official_source_found": False,
                "high_stakes_mode": False,
                "uncertainty_flags": ["clarification_needed"],
            },
            "route": route,
            "route_label": route,
            "warnings": ["The request was ambiguous without enough context."],
            "metadata": {
                "fallback_reason": "clarification",
                "original_query": str(query or "").strip(),
                "contract_version": "2026-04-24",
            },
        }
    )
