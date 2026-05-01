import pytest

from taos.core.workflows.engine import WorkflowEngine
from taos.core.workflows.models import Workflow


@pytest.mark.asyncio
async def test_workflow_engine_linear_success():
    workflow = Workflow.from_dict(
        {
            "name": "linear",
            "status": "active",
            "nodes": [
                {"id": "n1", "type": "trigger", "config": {}},
                {"id": "n2", "type": "research", "config": {"query": "gpt-4"}},
                {"id": "n3", "type": "action", "config": {"action": "notify"}},
            ],
            "edges": [
                {"from_node": "n1", "to_node": "n2"},
                {"from_node": "n2", "to_node": "n3"},
            ],
        }
    )
    engine = WorkflowEngine()
    run = await engine.run(workflow, initial_context={"input_query": "fallback"})
    assert run.status.value == "success"
    assert run.node_status["n1"] == "completed"
    assert run.node_status["n2"] == "completed"
    assert run.node_status["n3"] == "completed"


@pytest.mark.asyncio
async def test_workflow_engine_decision_branch():
    workflow = Workflow.from_dict(
        {
            "name": "decision",
            "status": "active",
            "nodes": [
                {"id": "start", "type": "trigger", "config": {}},
                {
                    "id": "judge",
                    "type": "decision",
                    "config": {"key": "input_query", "operator": "contains", "value": "bitcoin"},
                },
                {"id": "true_action", "type": "action", "config": {"action": "notify_true"}},
                {"id": "false_action", "type": "action", "config": {"action": "notify_false"}},
            ],
            "edges": [
                {"from_node": "start", "to_node": "judge"},
                {"from_node": "judge", "to_node": "true_action", "condition": "true"},
                {"from_node": "judge", "to_node": "false_action", "condition": "false"},
            ],
        }
    )
    engine = WorkflowEngine()
    run = await engine.run(workflow, initial_context={"input_query": "track bitcoin trend"})
    assert run.status.value == "success"
    assert run.node_status["true_action"] == "completed"
    assert "false_action" not in run.node_status

