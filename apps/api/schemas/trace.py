"""
TAOS API trace schemas.

These models describe the public-facing execution trace returned by
`/execute` when `include_trace=true`.
They intentionally exclude chain-of-thought, scratchpads, and other
private reasoning artifacts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


_REDACTION_KEYS = {
    "analysis",
    "chain_of_thought",
    "cot",
    "reasoning",
    "rationale",
    "scratchpad",
    "thought",
    "thoughts",
}


def _strip_sensitive_trace_fields(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _REDACTION_KEYS:
                continue
            cleaned[str(key)] = _strip_sensitive_trace_fields(item)
        return cleaned
    if isinstance(value, list):
        return [_strip_sensitive_trace_fields(item) for item in value]
    if isinstance(value, tuple):
        return [_strip_sensitive_trace_fields(item) for item in value]
    return value


class TraceEvent(BaseModel):
    """Backward-compatible event entry used by older trace payloads."""

    model_config = ConfigDict(extra="ignore")

    timestamp: datetime = Field(
        validation_alias=AliasChoices("timestamp", "ts"),
        description="UTC timestamp for the trace event.",
    )
    request_id: str = Field(description="Request identifier associated with the event.")
    event: str = Field(description="Trace event name.")
    stage: str = Field(description="Execution stage name.")
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Normalized, non-sensitive event payload.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_payload(cls, value: Any) -> Any:
        if isinstance(value, dict):
            payload = dict(value)
            if "timestamp" not in payload and "ts" in payload:
                payload["timestamp"] = payload.pop("ts")
            payload["data"] = _strip_sensitive_trace_fields(payload.get("data") or {})
            return payload
        return value


class FreshnessCheck(BaseModel):
    """Public freshness signal for research/news style requests."""

    model_config = ConfigDict(extra="ignore")

    status: str = Field(
        default="not_applicable",
        description="Freshness evaluation outcome: passed|recovered|failed|not_applicable.",
    )
    stale_phrase_detected: bool = Field(
        default=False,
        description="Whether stale fallback language was detected and handled.",
    )
    note: Optional[str] = Field(
        default=None,
        description="Optional note describing how freshness was handled.",
    )


class TraceNode(BaseModel):
    """A node executed inside a DAG step."""

    model_config = ConfigDict(extra="ignore")

    id: str
    type: str = "node"
    status: str = "success"
    tool: Optional[str] = None
    retries: int = 0
    latency_ms: float = 0.0
    summary: str = ""
    error: Optional[str] = None
    batch_index: Optional[int] = None
    frontier_index: Optional[int] = None


class TraceBatch(BaseModel):
    """A frontier batch executed inside a DAG step."""

    model_config = ConfigDict(extra="ignore")

    batch_index: int
    frontier_index: int
    status: str = "success"
    node_ids: List[str] = Field(default_factory=list)
    latency_ms: float = 0.0


class TraceStep(BaseModel):
    """A public execution step in the trace."""

    model_config = ConfigDict(extra="ignore")

    id: str
    type: str = "tool"
    status: str = "success"
    tool: Optional[str] = None
    retries: int = 0
    latency_ms: float = 0.0
    summary: str = ""
    error: Optional[str] = None
    execution_mode: Optional[str] = None
    frontier_count: Optional[int] = None
    batches: List[TraceBatch] = Field(default_factory=list)
    nodes: List[TraceNode] = Field(default_factory=list)


class TrustBlock(BaseModel):
    """User-facing trust summary derived from real runtime signals."""

    model_config = ConfigDict(extra="ignore")

    freshness: str = "Unknown"
    evidence: str = "Minimal"
    execution_path: str = "Direct"
    fallback_used: bool = False
    confidence: str = "Low"
    source_count: int = 0
    usable_sources_count: int = 0
    rejected_sources_count: int = 0
    last_verified: Optional[str] = None
    official_source_count: int = 0
    agreement: Optional[str] = None
    agreement_score: Optional[float] = None
    conflict_detected: Optional[bool] = None
    stale_detected: Optional[bool] = None
    signal: Optional[str] = None
    event_agreement: Optional[str] = None
    attribution_agreement: Optional[str] = None
    domain_diversity: Optional[float] = None
    extraction_quality: Optional[float] = None
    official_source_required: Optional[bool] = None
    official_source_found: Optional[bool] = None
    high_stakes_mode: Optional[bool] = None
    citation_coverage: Optional[float] = None
    supported_claims: Optional[int] = None
    partially_supported_claims: Optional[int] = None
    unsupported_claims: Optional[int] = None
    overall_support: Optional[str] = None
    confidence_reason: Optional[str] = None
    evidence_matrix_summary: Optional[Dict[str, Any]] = None
    answer_mode: Optional[str] = None
    requested_role: Optional[str] = None
    supported_role: Optional[str] = None
    selected_candidate: Optional[str] = None
    exact_role_verified: Optional[bool] = None
    role_match: Optional[bool] = None
    role_mismatch_reason: Optional[str] = None
    linkedin_source_found: Optional[bool] = None
    registry_source_found: Optional[bool] = None
    search_lanes_used: List[str] = Field(default_factory=list)
    source_tiers_found: List[str] = Field(default_factory=list)
    verification_state: Optional[str] = None
    uncertainty_flags: List[str] = Field(default_factory=list)


class ToneThresholds(BaseModel):
    """Runtime tuning thresholds for tone adaptation."""

    model_config = ConfigDict(extra="ignore")

    serious_override_threshold: Optional[float] = None
    emoji_allowed_serious_max: Optional[float] = None
    banter_allowed_playful_min: Optional[float] = None
    blend_current_weight: Optional[float] = None
    blend_prior_weight: Optional[float] = None
    decay_min_factor: Optional[float] = None


class ToneProfileTrace(BaseModel):
    """Sanitized tone profile used for user-facing response style adaptation."""

    model_config = ConfigDict(extra="ignore")

    current: Optional[str] = None
    blended: Optional[str] = None
    serious: Optional[float] = None
    casual: Optional[float] = None
    playful: Optional[float] = None
    emoji_allowed: Optional[bool] = None
    banter_allowed: Optional[bool] = None
    style_hint: Optional[str] = None
    thresholds: Optional[ToneThresholds] = None


class TraceResponse(BaseModel):
    """Optional sanitized execution trace attached to an agent response."""

    model_config = ConfigDict(extra="ignore")

    request_id: Optional[str] = Field(default=None, description="Trace request id.")
    intent: Optional[str] = Field(default=None, description="Detected intent.")
    mode: Optional[str] = Field(default=None, description="Selected execution mode.")
    query_kind: Optional[str] = Field(
        default=None,
        description="Internal query-kind classification (for example: entity_lookup|research|document_qa|general).",
    )
    verification_state: Optional[str] = Field(
        default=None,
        description="Verification state used by trust layer (for example: confirmed|partially_confirmed|not_verified).",
    )
    policy_reason: Optional[str] = Field(
        default=None,
        description="Primary route policy reason after overrides/verifier decisions.",
    )
    route_label: Optional[str] = Field(
        default=None,
        description="Resolved route label: fast_message|no_search|fast_search|deep_search|news_search|official_search|comparison_search|standard_task|task|doc_mode|clarification.",
    )
    public_route_label: Optional[str] = Field(
        default=None,
        description="Stable public-facing route label used by the frontend trace and route badge UI.",
    )
    selected_route: Optional[str] = Field(
        default=None,
        description="Final selected backend execution route after route-owner resolution.",
    )
    original_route_hint: Optional[str] = Field(
        default=None,
        description="Original route hint before execution collapsing, such as news_search -> deep_research.",
    )
    owner: Optional[str] = Field(
        default=None,
        description="Public-safe route owner label used by the frontend trace.",
    )
    route_owner: Optional[str] = Field(
        default=None,
        description="Backend route owner label for the selected route.",
    )
    route_source: Optional[str] = Field(
        default=None,
        description="Routing source: heuristic|semantic_router|semantic_guard|override|fallback.",
    )
    route_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Router confidence score when available.",
    )
    policy_override_reasons: List[str] = Field(
        default_factory=list,
        description="Policy override reasons applied after routing, when present.",
    )
    doc_context_active: Optional[bool] = Field(
        default=None,
        description="Whether an active uploaded document context was present during routing.",
    )
    interpretation: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Internal interpretation envelope (raw/normalized/rewritten + language/routing/decision).",
    )
    routing_profile: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Soft routing profile including intent scores, ambiguity, grounding need, and risk level.",
    )
    route_decision: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Route selector + verifier outcome (selected route, reason, verification, reroute flag).",
    )
    route_boundary_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Phase 107 route ownership and boundary guard summary.",
    )
    query_frame: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Observe-only QueryFrame runtime summary for route-alignment debugging.",
    )
    query_frame_mismatch_count: Optional[int] = Field(default=0, description="Observe-only QueryFrame mismatch counter for this request.")
    query_frame_aligned_count: Optional[int] = Field(default=0, description="Observe-only QueryFrame aligned counter for this request.")
    query_frame_unknown_count: Optional[int] = Field(default=0, description="Observe-only QueryFrame unknown-alignment counter for this request.")
    query_frame_supported_multilingual_count: Optional[int] = Field(default=0, description="Observe-only QueryFrame supported multilingual signal counter for this request.")
    query_frame_semantic_fallback_used_count: Optional[int] = Field(default=0, description="Observe-only count for semantic canonicalizer fallback usage.")
    query_frame_fastpath_used_count: Optional[int] = Field(default=0, description="Observe-only count for deterministic fast-path canonicalizer usage.")
    query_frame_low_confidence_count: Optional[int] = Field(default=0, description="Observe-only count for low-confidence canonicalizer outcomes.")
    query_frame_entity_handoff_enabled: Optional[bool] = Field(default=False, description="Whether QueryFrame entity handoff feature is enabled.")
    query_frame_entity_handoff_applied: Optional[bool] = Field(default=False, description="Whether QueryFrame entity handoff was applied.")
    query_frame_entity_handoff_blocked_reason: Optional[str] = Field(default="", description="Blocked reason when QueryFrame entity handoff did not apply.")
    entity_handoff_source: Optional[str] = Field(default="", description="Entity handoff source: query_frame or legacy_resolver.")
    entity_handoff_lookup_type: Optional[str] = Field(default="", description="Lookup type used by entity handoff.")
    entity_handoff_entity_name: Optional[str] = Field(default="", description="Entity name used by handoff.")
    entity_handoff_requested_role: Optional[str] = Field(default="", description="Requested role used by handoff.")
    entity_handoff_answer_language: Optional[str] = Field(default="", description="Answer language implied by handoff.")
    entity_search_queries_generated: List[str] = Field(default_factory=list, description="Entity search queries generated by handoff or legacy path.")
    entity_search_query_lanes: List[str] = Field(default_factory=list, description="Lane labels for generated entity search queries.")
    legacy_entity_resolver_used: Optional[bool] = Field(default=True, description="Whether legacy entity resolver path was used.")
    planning_handoff: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Planner handoff payload carrying raw/normalized/rewritten query and routing constraints.",
    )
    planner_path: Optional[str] = Field(
        default=None,
        description="Chosen execution path: fast_path|no_search|fast_search|dynamic_lookup|fsm|dag_exec|deep_research|document_intelligence.",
    )
    dag_name: Optional[str] = Field(default=None, description="Executed DAG name when applicable.")
    pipeline_stages: List[str] = Field(
        default_factory=list,
        description="Compact runtime pipeline labels for inspector UX.",
    )
    tone_profile: Optional[ToneProfileTrace] = Field(
        default=None,
        description="Runtime tone profile summary with guardrail thresholds.",
    )
    provider_health: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Provider circuit-breaker state and fallback/cache usage summary.",
    )
    usage: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Route-level usage and estimated cost summary.",
    )
    quota: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Route-level quota decision and configured budget.",
    )
    fsm_transitions: List[str] = Field(
        default_factory=list,
        description="FSM transitions taken during execution.",
    )
    steps: List[TraceStep] = Field(
        default_factory=list,
        description="Sanitized step-level execution summary.",
    )
    fallback_used: bool = Field(default=False, description="Whether a fallback path was used.")
    fallback_reason: Optional[str] = Field(
        default=None,
        description="Reason for fallback, when applicable.",
    )
    freshness_check: FreshnessCheck = Field(
        default_factory=FreshnessCheck,
        description="Freshness verification outcome for time-sensitive requests.",
    )
    freshness_summary: Optional[Dict[str, Any]] = Field(default=None)
    evidence_selection_summary: Optional[Dict[str, Any]] = Field(default=None)
    citation_plan_summary: Optional[Dict[str, Any]] = Field(default=None)
    diversity_summary: Optional[Dict[str, Any]] = Field(default=None)
    conflict_summary: Optional[Dict[str, Any]] = Field(default=None)
    high_stakes_summary: Optional[Dict[str, Any]] = Field(default=None)
    cache_summary: Optional[Dict[str, Any]] = Field(default=None)
    document_summary: Optional[Dict[str, Any]] = Field(default=None)
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Final confidence score for the response.",
    )
    timing: Dict[str, Any] = Field(
        default_factory=dict,
        description="Latency metrics emitted by runtime (route/llm/total timings).",
    )
    streaming_started_at: Optional[float] = Field(
        default=None,
        description="Unix timestamp when SSE streaming started for this request.",
    )
    first_event_latency_ms: Optional[float] = Field(
        default=None,
        description="Latency from stream start to the first visible event.",
    )
    budget_exceeded: Optional[bool] = Field(
        default=None,
        description="Whether a runtime/request budget was exceeded.",
    )
    timeout_stage: Optional[str] = Field(
        default=None,
        description="Stage name associated with the timeout or budget cutoff.",
    )
    partial_answer_used: Optional[bool] = Field(
        default=None,
        description="Whether a partial answer was returned after timeout or fallback.",
    )
    stage_budgets: Optional[Dict[str, int]] = Field(
        default=None,
        description="Route-aware stage latency budgets in milliseconds.",
    )
    stage_timings: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Public stage timing rows with duration, budget, and exceeded flag.",
    )
    public_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Clean user-facing trace summary for route, research, trust, and latency.",
    )
    trust_block: Optional[TrustBlock] = Field(
        default=None,
        description="User-facing trust summary derived from runtime signals.",
    )
    entity_intelligence_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Public-safe entity lookup summary including selected candidate, role verification, and source-tier signals.",
    )
    evidence_matrix_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Summary of claim-level support analysis against gathered evidence.",
    )
    count: int = Field(
        default=0,
        ge=0,
        description="Backward-compatible count of raw trace events when included.",
    )
    events: List[TraceEvent] = Field(
        default_factory=list,
        validation_alias=AliasChoices("events", "traces"),
        description="Optional backward-compatible sanitized trace events.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_payload(cls, value: Any) -> Any:
        if isinstance(value, list):
            return {"events": value, "count": len(value)}
        if isinstance(value, dict):
            payload = dict(value)
            if "events" not in payload and "traces" in payload:
                payload["events"] = payload.pop("traces")
            if "count" not in payload and isinstance(payload.get("events"), list):
                payload["count"] = len(payload["events"])
            if "freshness_check" not in payload:
                payload["freshness_check"] = {}
            for list_field in (
                "policy_override_reasons",
                "pipeline_stages",
                "fsm_transitions",
                "steps",
                "stage_timings",
                "events",
            ):
                if payload.get(list_field) is None:
                    payload[list_field] = []
            return payload
        return value
