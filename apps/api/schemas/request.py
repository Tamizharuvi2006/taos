"""
TAOS API Request Schemas — Pydantic models for API input validation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class ExecuteRequest(BaseModel):
    """POST /execute — Run an agent task."""

    goal: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="The task/goal for the agent to execute",
        examples=["Search for the latest Python release and summarize the key features"],
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Optional request ID for tracking/idempotency",
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
    config_overrides: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional config overrides (max_steps, cost_budget, etc.)",
    )
    doc_context_active: bool = Field(
        default=False,
        description="Whether an active uploaded document context exists for this request.",
    )

    @field_validator("goal")
    @classmethod
    def validate_goal_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Goal cannot be empty or whitespace")
        return v.strip()


class PlanRequest(BaseModel):
    """POST /plan — Generate a plan without executing."""

    goal: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="The goal to generate a plan for",
    )

    @field_validator("goal")
    @classmethod
    def validate_goal_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Goal cannot be empty or whitespace")
        return v.strip()


class BatchRequest(BaseModel):
    """POST /batch — Execute multiple goals."""

    goals: List[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="List of goals to execute sequentially",
    )
    stop_on_failure: bool = Field(
        default=False,
        description="Stop processing on first failure",
    )
