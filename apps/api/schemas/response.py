"""
TAOS API Response Schemas — Pydantic models for API output.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StepResultResponse(BaseModel):
    """A single step result in the response."""

    step_id: str
    tool: Optional[str] = None
    success: bool
    result_summary: Optional[str] = None
    error: Optional[str] = None
    latency: float = 0.0
    cost: float = 0.0
    retries_used: int = 0


class CostBreakdownResponse(BaseModel):
    """Cost breakdown in the response."""

    planner_cost: float = 0.0
    execution_cost: float = 0.0
    tool_cost: float = 0.0
    reflection_cost: float = 0.0


class ExecuteResponse(BaseModel):
    """Response for POST /execute."""

    request_id: str
    goal: str
    success: bool
    status: str
    result: Optional[Any] = None
    steps_executed: int = 0
    total_cost: float = 0.0
    cost_breakdown: CostBreakdownResponse = Field(
        default_factory=CostBreakdownResponse
    )
    confidence: float = 0.0
    error: Optional[str] = None
    step_results: List[Any] = Field(default_factory=list)
    elapsed_time: float = 0.0
    state_version: int = 0
    replan_count: int = 0
    output_redactions: int = 0


class PlanStepResponse(BaseModel):
    """A single plan step in the response."""

    id: str
    action: str
    tool: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    depends_on: List[str] = Field(default_factory=list)
    description: str = ""


class PlanResponse(BaseModel):
    """Response for POST /plan."""

    goal: str
    plan: Dict[str, Any]
    planning_cost: float = 0.0
    complexity: str = "low"
    estimated_steps: int = 1
    suggested_tools: List[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """Response for GET /health."""

    status: str = "healthy"
    version: str = "0.1.0"
    uptime_seconds: float = 0.0
    environment: str = "development"
    ready: bool = True
    checks: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Standard error response."""

    error_code: str
    message: str
    request_id: str
