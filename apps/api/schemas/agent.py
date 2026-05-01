"""
TAOS API - Simplified Agent Execute Endpoint.

POST /execute - The primary endpoint per the Backend Implementation Report.

Request:  {"query": "string"}
Response: {"answer": "...", "intent": "...", "domain": "...", "mode": "...",
           "confidence": 0.0-1.0, "sources": [], ...}
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from taos.apps.api.schemas.trace import TraceResponse, TrustBlock


class AgentRequest(BaseModel):
    """POST /execute - Simple agent query."""

    query: str = Field(
        ...,
        min_length=2,
        max_length=2000,
        description="The developer query",
        examples=[
            "React vs Vue performance",
            "current version of vite",
            "fix module not found error",
        ],
    )
    user_id: str = Field(
        default="default",
        min_length=1,
        max_length=128,
        description="Tenant/user scope for persistence and feedback memory.",
    )
    user_tier: str = Field(
        default="free",
        description="Usage tier for rate limiting/quota checks: free|paid|enterprise",
    )
    chat_id: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=256,
        description="Optional chat/session id used for per-session tone smoothing.",
    )
    include_trace: bool = Field(
        default=False,
        description="Include a sanitized execution trace in the response.",
    )
    doc_context_active: bool = Field(
        default=False,
        description="Whether the current chat has an active uploaded document context.",
    )
    doc_ids: List[str] = Field(
        default_factory=list,
        description="Optional attached document IDs for retrieval-grounded doc-mode execution.",
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Query cannot be empty")
        return v.strip()


class AgentResponse(BaseModel):
    """Response from POST /execute."""

    class TrustBadge(BaseModel):
        key: str
        label: str
        value: str
        tone: str = "neutral"

    class UncertaintyBox(BaseModel):
        level: str = "caution"
        title: str
        messages: List[str] = Field(default_factory=list)
        high_stakes: bool = False

    class SourceCard(BaseModel):
        title: str
        url: str
        domain: str
        published_at: Optional[str] = None
        source_type: str = "reporting"
        quality_hint: Optional[str] = None

    class AnswerSection(BaseModel):
        key: str
        title: str
        content: str = ""
        bullets: List[str] = Field(default_factory=list)

    class FrontendHints(BaseModel):
        route_label: str = "standard_task"
        progress_stages: List[str] = Field(default_factory=list)
        high_stakes: bool = False
        official_source_required: bool = False
        official_source_found: bool = False
        query_kind: Optional[str] = None
        verification_state: Optional[str] = None
        policy_reason: Optional[str] = None

    answer: str = Field(description="Formatted answer (answer-first)")
    direct_answer: str = Field(default="", description="Short direct answer")
    key_points: List[str] = Field(default_factory=list, description="Key bullet points")
    intent: str = Field(default="task", description="Detected intent type")
    domain: str = Field(default="general", description="Detected domain")
    mode: str = Field(default="standard", description="Execution mode used")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sources: List[str] = Field(default_factory=list, description="Source URLs")
    fast_path: bool = Field(default=False, description="True if fast path was used")
    evaluation: Optional[Dict[str, Any]] = Field(default=None, description="Self-eval scores")
    steps_executed: int = Field(default=0)
    cost: float = Field(default=0.0)
    elapsed_ms: float = Field(default=0.0)
    error: Optional[str] = Field(default=None)

    # Internals (optional debug)
    request_id: Optional[str] = None
    replan_count: int = 0
    trace: Optional[TraceResponse] = Field(
        default=None,
        description="Optional sanitized execution trace returned when requested.",
    )
    trust_block: Optional[TrustBlock] = Field(
        default=None,
        description="User-facing trust summary derived from runtime signals.",
    )
    trust_badges: List[TrustBadge] = Field(
        default_factory=list,
        description="Frontend-friendly trust chips (freshness, agreement, conflict, high-stakes).",
    )
    uncertainty_box: Optional[UncertaintyBox] = Field(
        default=None,
        description="Calm caution block for weak/conflicting/high-stakes evidence cases.",
    )
    source_cards: List[SourceCard] = Field(
        default_factory=list,
        description="Rich source card metadata for frontend rendering.",
    )
    answer_sections: List[AnswerSection] = Field(
        default_factory=list,
        description="Structured sections parsed from answer text for UI rendering.",
    )
    sections: List[AnswerSection] = Field(
        default_factory=list,
        description="Unified response-contract alias for answer sections.",
    )
    route: str = Field(
        default="standard_task",
        description="Unified route label exposed to all clients: fast_message|no_search|fast_search|deep_search|news_search|official_search|comparison_search|standard_task|task|doc_mode|clarification.",
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="User-facing warnings derived from uncertainty, fallback, or timeout conditions.",
    )
    evidence_matrix_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Claim-support summary derived from the evidence matrix.",
    )
    freshness_summary: Optional[Dict[str, Any]] = Field(default=None)
    evidence_selection_summary: Optional[Dict[str, Any]] = Field(default=None)
    citation_plan_summary: Optional[Dict[str, Any]] = Field(default=None)
    diversity_summary: Optional[Dict[str, Any]] = Field(default=None)
    conflict_summary: Optional[Dict[str, Any]] = Field(default=None)
    high_stakes_summary: Optional[Dict[str, Any]] = Field(default=None)
    cache_summary: Optional[Dict[str, Any]] = Field(default=None)
    document_summary: Optional[Dict[str, Any]] = Field(default=None)
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Stable metadata surface for route/debug/fallback context.",
    )
    frontend_hints: Optional[FrontendHints] = Field(
        default=None,
        description="Rendering hints for route/progress/high-stakes emphasis in frontend.",
    )

    @model_validator(mode="before")
    @classmethod
    def sync_contract_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        sections = payload.get("sections")
        answer_sections = payload.get("answer_sections")
        if sections and not answer_sections:
            payload["answer_sections"] = sections
        elif answer_sections and not sections:
            payload["sections"] = answer_sections

        if not payload.get("route"):
            hints = payload.get("frontend_hints")
            if isinstance(hints, dict):
                payload["route"] = str(hints.get("route_label") or "standard_task")
            else:
                payload["route"] = str(payload.get("route_label") or "standard_task")
        if payload.get("warnings") is None:
            payload["warnings"] = []
        if payload.get("metadata") is None:
            payload["metadata"] = {}
        return payload
