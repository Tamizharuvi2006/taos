"""Persistent workflow manager."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from taos.core.workflows.engine import WorkflowEngine
from taos.core.workflows.models import Workflow, WorkflowRun
from taos.infra.logging.logger import TAOSLogger
from taos.infra.persistence.store import StorageBackend
from taos.infra.persistence.shared_store import get_shared_store


WORKFLOWS_COLLECTION = "workflows"
WORKFLOW_RUNS_COLLECTION = "workflow_runs"


class PersistentWorkflowManager:
    def __init__(
        self,
        user_id: str,
        store: Optional[StorageBackend] = None,
        logger: Optional[TAOSLogger] = None,
    ) -> None:
        self._user_id = user_id
        if store is not None:
            self._store = store
        else:
            self._store = get_shared_store()
        self._engine = WorkflowEngine(logger=logger)
        self._logger = logger or TAOSLogger(name="taos.pworkflow")

    async def create_workflow(
        self,
        name: str,
        description: str,
        trigger: Dict[str, Any],
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        tags: Optional[List[str]] = None,
    ) -> Workflow:
        workflow = Workflow.from_dict(
            {
                "name": name,
                "description": description,
                "status": "active",
                "trigger": trigger,
                "nodes": nodes,
                "edges": edges,
                "tags": tags or [],
            }
        )
        await self._store.set(
            WORKFLOWS_COLLECTION,
            workflow.workflow_id,
            workflow.to_dict(),
            user_id=self._user_id,
        )
        return workflow

    async def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        data = await self._store.get(WORKFLOWS_COLLECTION, workflow_id, user_id=self._user_id)
        if not data:
            return None
        return Workflow.from_dict(data)

    async def list_workflows(self, limit: int = 100) -> List[Workflow]:
        docs = await self._store.list(WORKFLOWS_COLLECTION, user_id=self._user_id, limit=limit)
        workflows = [Workflow.from_dict(doc) for doc in docs]
        return sorted(workflows, key=lambda item: item.created_at, reverse=True)

    async def delete_workflow(self, workflow_id: str) -> bool:
        return await self._store.delete(WORKFLOWS_COLLECTION, workflow_id, user_id=self._user_id)

    async def run_workflow(
        self,
        workflow_id: str,
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> WorkflowRun:
        workflow = await self.get_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow not found: {workflow_id}")

        run = await self._engine.run(
            workflow,
            initial_context=initial_context,
            user_id=self._user_id,
        )
        await self._store.set(
            WORKFLOW_RUNS_COLLECTION,
            run.run_id,
            run.to_dict(),
            user_id=self._user_id,
        )
        return run

    async def get_run_history(self, workflow_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        docs = await self._store.list(
            WORKFLOW_RUNS_COLLECTION,
            user_id=self._user_id,
            filters={"workflow_id": workflow_id},
            limit=limit,
        )
        return sorted(docs, key=lambda item: item.get("started_at", 0), reverse=True)
