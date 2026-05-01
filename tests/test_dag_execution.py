from __future__ import annotations

import pytest

from taos.core.execution.dag_models import DagDefinition, DagNode, DagNodeKind
from taos.core.execution.dag_registry import DagRegistry
from taos.core.execution.dag_runner import DagRunner
from taos.core.planner.plan_validator import PlanValidator
from taos.core.state.state_schema import PlanObject, PlanStep, StepResult, StepType
from taos.core.execution.step_runner import StepRunner


class _FakeToolExecutor:
    async def execute(self, tool_name: str, tool_input=None, task_id: str = "", step_id: str = "") -> StepResult:
        tool_input = tool_input or {}
        if tool_name == "web_search":
            return StepResult(
                step_id=step_id,
                success=True,
                tool_name=tool_name,
                cost=0.001,
                result={
                    "query": tool_input.get("query", ""),
                    "results": [
                        {
                            "title": "Source A",
                            "link": "https://example.com/a",
                            "snippet": "Snippet A",
                        },
                        {
                            "title": "Source B",
                            "link": "https://example.com/b",
                            "snippet": "Snippet B",
                        },
                    ],
                },
            )
        if tool_name == "web_extract":
            return StepResult(
                step_id=step_id,
                success=True,
                tool_name=tool_name,
                cost=0.0,
                result={
                    "success": True,
                    "url": tool_input.get("url", ""),
                    "title": "Example Title",
                    "published_at": "2026-04-07T10:00:00Z",
                    "text": "Extracted body text",
                },
            )
        return StepResult(
            step_id=step_id,
            success=False,
            tool_name=tool_name,
            error=f"Unknown fake tool: {tool_name}",
            error_type="TOOL_FAILURE",
        )


class _SelectiveFailToolExecutor(_FakeToolExecutor):
    def __init__(self, fail_tools: set[str]) -> None:
        self._fail_tools = fail_tools

    async def execute(self, tool_name: str, tool_input=None, task_id: str = "", step_id: str = "") -> StepResult:
        if tool_name in self._fail_tools:
            return StepResult(
                step_id=step_id,
                success=False,
                tool_name=tool_name,
                error=f"Injected failure for {tool_name}",
                error_type="TOOL_FAILURE",
            )
        return await super().execute(tool_name=tool_name, tool_input=tool_input, task_id=task_id, step_id=step_id)


def test_plan_step_normalizes_dag_exec_tool():
    step = PlanStep(
        id="s1",
        action="run dag",
        tool="dag_exec",
        tool_input={"query": "hello"},
    )
    assert step.step_type == StepType.DAG_EXEC
    assert step.tool is None
    assert step.dag_input_template == {"query": "hello"}


def test_plan_validator_requires_dag_name_for_dag_step():
    validator = PlanValidator(registered_tools={"web_search", "web_extract"})
    plan = PlanObject(
        steps=[
            PlanStep(
                id="s1",
                action="run dag",
                step_type=StepType.DAG_EXEC,
                tool=None,
                dag_name=None,
            )
        ]
    )
    result = validator.validate(plan)
    assert result.is_valid is False
    assert any("dag_name" in e for e in result.errors)


@pytest.mark.asyncio
async def test_dag_runner_executes_research_v2():
    fake_tools = _FakeToolExecutor()
    runner = DagRunner(tool_executor=fake_tools)  # type: ignore[arg-type]
    out = await runner.run(
        dag_name="research_v2",
        input_data={
            "query": "iran israel latest",
            "latest_query": "iran israel latest updates",
            "context_query": "iran israel background context",
            "official_query": "iran israel official statements",
            "search_type": "news",
            "recency_days": 2,
        },
        task_id="t1",
        parent_step_id="step_1",
    )
    assert out.status == "success"
    assert out.execution_mode == "parallel"
    assert out.frontier_count >= 2
    assert any(len(batch["node_ids"]) > 1 for batch in out.batches)
    assert "compose" in out.final_output
    assert out.final_output["compose"]["source_count"] >= 1


@pytest.mark.asyncio
async def test_step_runner_routes_dag_exec_steps():
    fake_tools = _FakeToolExecutor()
    runner = StepRunner(tool_executor=fake_tools)  # type: ignore[arg-type]
    step = PlanStep(
        id="s_dag",
        action="Research current status",
        step_type=StepType.DAG_EXEC,
        dag_name="research_v2",
        dag_input_template={"query": "iran israel current status", "search_type": "news", "recency_days": 2},
    )
    result = await runner.run(step=step, context={"goal": "iran israel current status"}, task_id="task_1")
    assert result.success is True
    assert result.tool_name == "dag:research_v2"
    assert isinstance(result.result, dict)
    assert result.result.get("status") == "success"


@pytest.mark.asyncio
async def test_parallel_dag_frontiers_respect_dependencies():
    fake_tools = _FakeToolExecutor()
    registry = DagRegistry()
    registry.register(
        DagDefinition(
            name="parallel_test",
            nodes=[
                DagNode(id="root", kind=DagNodeKind.TOOL, tool="web_search"),
                DagNode(id="left", kind=DagNodeKind.TOOL, tool="web_search", depends_on=["root"]),
                DagNode(id="right", kind=DagNodeKind.TOOL, tool="web_search", depends_on=["root"]),
                DagNode(
                    id="join",
                    kind=DagNodeKind.TRANSFORM,
                    depends_on=["left", "right"],
                    inputs={"mode": "research_brief", "goal": "g", "extract_paths": []},
                ),
            ],
            output_node_ids=["join"],
        )
    )
    runner = DagRunner(tool_executor=fake_tools, registry=registry)  # type: ignore[arg-type]

    out = await runner.run(dag_name="parallel_test", input_data={"query": "x"}, task_id="t1", parent_step_id="p1")

    assert out.status == "success"
    assert out.frontier_count >= 3
    root_frontier = out.node_results["root"].frontier_index
    left_frontier = out.node_results["left"].frontier_index
    right_frontier = out.node_results["right"].frontier_index
    join_frontier = out.node_results["join"].frontier_index
    assert root_frontier == 1
    assert left_frontier == right_frontier
    assert left_frontier > root_frontier
    assert join_frontier > left_frontier


@pytest.mark.asyncio
async def test_required_node_failure_aborts_dag_with_partial_outputs():
    fake_tools = _SelectiveFailToolExecutor(fail_tools={"bad_tool"})
    registry = DagRegistry()
    registry.register(
        DagDefinition(
            name="required_failure_test",
            nodes=[
                DagNode(id="root", kind=DagNodeKind.TOOL, tool="web_search"),
                DagNode(id="must_fail", kind=DagNodeKind.TOOL, tool="bad_tool", depends_on=["root"], required=True),
                DagNode(id="optional_after", kind=DagNodeKind.TOOL, tool="web_search", depends_on=["must_fail"], required=False),
            ],
            output_node_ids=["root", "must_fail"],
        )
    )
    runner = DagRunner(tool_executor=fake_tools, registry=registry)  # type: ignore[arg-type]

    out = await runner.run(dag_name="required_failure_test", input_data={"query": "x"}, task_id="t2", parent_step_id="p2")

    assert out.status == "failed"
    assert "must_fail" in out.node_results
    assert out.node_results["must_fail"].status == "failed"
    assert "Injected failure" in (out.error or "")
    assert out.final_output.get("root") is not None
