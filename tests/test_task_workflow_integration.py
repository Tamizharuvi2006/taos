import pytest

from taos.core.tasks.persistent_manager import PersistentTaskManager
from taos.core.tasks.task_model import TriggerType
from taos.core.workflows.persistent_manager import PersistentWorkflowManager
from taos.infra.persistence.store import InMemoryStore


@pytest.mark.asyncio
async def test_task_executes_attached_workflow():
    store = InMemoryStore()
    user_id = "u_test"

    workflow_manager = PersistentWorkflowManager(user_id=user_id, store=store)
    workflow = await workflow_manager.create_workflow(
        name="wf1",
        description="test wf",
        trigger={"type": "manual"},
        nodes=[
            {"id": "start", "type": "trigger", "config": {}},
            {"id": "act", "type": "action", "config": {"action": "notify"}},
        ],
        edges=[{"from_node": "start", "to_node": "act"}],
    )

    task_manager = PersistentTaskManager(store=store, user_id=user_id)
    task = await task_manager.create_task(
        goal="Run attached workflow",
        trigger_type=TriggerType.MANUAL,
        workflow_id=workflow.workflow_id,
        workflow_context={"input_query": "hello"},
    )

    execution = await task_manager.execute_task(task.task_id)
    assert execution.success is True
    assert "Workflow" in str(execution.result)

