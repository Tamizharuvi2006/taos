"""Planner microservice app."""

from __future__ import annotations

from fastapi import APIRouter

from taos.apps.services.common import create_service_app
from taos.core.planner.planner import Planner
from taos.core.services.contracts import PlannerServiceRequest, PlannerServiceResponse
from taos.core.tools.builtin import register_all_builtin_tools
from taos.core.tools.registry import ToolRegistry

app = create_service_app("TAOS Planner Service")
router = APIRouter(prefix="/planner", tags=["planner"])

_tool_registry = ToolRegistry()
register_all_builtin_tools(_tool_registry)
_planner = Planner(available_tools=_tool_registry.list_names())


@router.post("/generate", response_model=PlannerServiceResponse)
async def generate_plan(req: PlannerServiceRequest) -> PlannerServiceResponse:
    plan, planning_cost = await _planner.generate_plan(
        goal=req.goal,
        context=req.context,
        max_steps=req.max_steps,
        cost_budget=req.cost_budget,
        intent=req.intent,
        available_tools_override=req.available_tools_override,
    )
    return PlannerServiceResponse(
        plan=plan.model_dump(),
        planning_cost=planning_cost,
    )


@router.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "planner"}


app.include_router(router)
