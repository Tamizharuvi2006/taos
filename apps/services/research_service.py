"""Research microservice app."""

from __future__ import annotations

from fastapi import APIRouter

from taos.apps.services.common import create_service_app
from taos.core.agents.research_agent import ResearchAgent
from taos.core.execution.executor import Executor
from taos.core.execution.step_runner import StepRunner
from taos.core.services.contracts import StepServiceRequest, StepServiceResponse
from taos.core.state.state_schema import GlobalState, PlanStep
from taos.core.tools.builtin import register_all_builtin_tools
from taos.core.tools.registry import ToolRegistry
from taos.core.tools.source_ranker import SourceRanker
from taos.core.tools.tool_executor import ToolExecutor

app = create_service_app("TAOS Research Service")
router = APIRouter(prefix="/research", tags=["research"])

_tool_registry = ToolRegistry()
register_all_builtin_tools(_tool_registry)
_research_agent = ResearchAgent(
    executor=Executor(StepRunner(ToolExecutor(_tool_registry))),
    source_ranker=SourceRanker(),
)


@router.post("/run", response_model=StepServiceResponse)
async def run_step(req: StepServiceRequest) -> StepServiceResponse:
    step = PlanStep.model_validate(req.step)
    state = GlobalState.model_validate(req.state)
    step_result = await _research_agent.execute(
        step=step,
        state=state,
        step_index=req.step_index,
    )
    return StepServiceResponse(
        step_result=step_result.model_dump(),
        service="research_service",
    )


@router.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "research"}


app.include_router(router)
