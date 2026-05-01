from __future__ import annotations

import pytest

from taos.core.services.clients import AgentServiceClient, ServiceClientError
from taos.core.services.contracts import PlannerServiceRequest
from taos.core.state.state_schema import GlobalState, PlanObject, PlanStep, StepResult


@pytest.mark.asyncio
async def test_service_client_parses_planner_response(monkeypatch):
    client = AgentServiceClient()

    async def _fake_post_json(*args, **kwargs):
        return {
            "plan": {
                "steps": [{"id": "s1", "action": "search", "tool": "web_search", "tool_input": {"query": "x"}, "depends_on": [], "description": "d"}],
                "max_steps": 5,
                "cost_budget": 1.0,
            },
            "planning_cost": 0.01,
            "service": "planner_service",
        }

    monkeypatch.setattr(client, "_post_json", _fake_post_json)
    plan, cost = await client.generate_plan(
        PlannerServiceRequest(goal="test", context=[]),
        request_id="rid1",
    )
    assert isinstance(plan, PlanObject)
    assert plan.steps[0].tool == "web_search"
    assert cost == 0.01


@pytest.mark.asyncio
async def test_service_client_parses_step_response(monkeypatch):
    client = AgentServiceClient()
    step = PlanStep(action="search", tool="web_search", tool_input={"query": "q"})
    state = GlobalState(goal="g")

    async def _fake_post_json(*args, **kwargs):
        return {
            "step_result": {
                "step_id": step.id,
                "success": True,
                "result": "ok",
                "tool_name": "web_search",
            },
            "service": "execution_service",
        }

    monkeypatch.setattr(client, "_post_json", _fake_post_json)
    result = await client.run_execution_step(step, state, 0, request_id="rid2")
    assert isinstance(result, StepResult)
    assert result.success is True


@pytest.mark.asyncio
async def test_engine_planner_adapter_fallback(monkeypatch):
    from taos.orchestration.engine import OrchestrationEngine

    engine = OrchestrationEngine()
    monkeypatch.setattr(engine._settings, "microservices_enabled", True)
    monkeypatch.setattr(engine._settings, "enable_planner_service", True)

    async def _raise(*args, **kwargs):
        raise ServiceClientError("down")

    async def _local_generate_plan(*args, **kwargs):
        return PlanObject(steps=[PlanStep(action="local step")]), 0.02

    monkeypatch.setattr(engine._service_client, "generate_plan", _raise)
    monkeypatch.setattr(engine._planner_agent, "generate_plan", _local_generate_plan)

    plan, cost = await engine._generate_plan_with_adapter(
        goal="x",
        context=[],
        intent="task",
        available_tools_override=None,
        request_id="rid3",
    )
    assert len(plan.steps) == 1
    assert cost == 0.02
