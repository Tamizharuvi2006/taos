"""Shared contracts for TAOS distributed agent services."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PlannerServiceRequest(BaseModel):
    goal: str
    context: List[str] = Field(default_factory=list)
    intent: Optional[str] = None
    available_tools_override: Optional[List[str]] = None
    max_steps: Optional[int] = None
    cost_budget: Optional[float] = None


class PlannerServiceResponse(BaseModel):
    plan: Dict[str, Any]
    planning_cost: float
    service: str = "planner_service"


class StepServiceRequest(BaseModel):
    step: Dict[str, Any]
    state: Dict[str, Any]
    step_index: int


class StepServiceResponse(BaseModel):
    step_result: Dict[str, Any]
    service: str
