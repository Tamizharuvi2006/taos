"""Workflow routes for DAG automation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from taos.apps.api.auth_context import require_uid
from taos.core.workflows.persistent_manager import PersistentWorkflowManager


router = APIRouter(prefix="/workflows", tags=["workflows"])
_workflow_managers: Dict[str, PersistentWorkflowManager] = {}


def get_workflow_manager(user_id: str) -> PersistentWorkflowManager:
    if user_id not in _workflow_managers:
        _workflow_managers[user_id] = PersistentWorkflowManager(user_id=user_id)
    return _workflow_managers[user_id]


class WorkflowNodeInput(BaseModel):
    id: str
    type: str
    config: Dict[str, Any] = Field(default_factory=dict)


class WorkflowEdgeInput(BaseModel):
    from_node: str
    to_node: str
    condition: Optional[str] = None


class CreateWorkflowRequest(BaseModel):
    name: str = Field(..., min_length=2)
    description: str = ""
    trigger: Dict[str, Any] = Field(default_factory=dict)
    nodes: List[WorkflowNodeInput]
    edges: List[WorkflowEdgeInput] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class WorkflowResponse(BaseModel):
    workflow_id: str
    name: str
    description: str
    status: str
    trigger: Dict[str, Any]
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    created_at: float
    updated_at: float
    tags: List[str]


class RunWorkflowRequest(BaseModel):
    context: Dict[str, Any] = Field(default_factory=dict)


class RunWorkflowResponse(BaseModel):
    run_id: str
    workflow_id: str
    status: str
    started_at: float
    finished_at: float
    node_status: Dict[str, str]
    context: Dict[str, Any]
    error: str = ""


@router.post("", response_model=WorkflowResponse)
async def create_workflow(request: CreateWorkflowRequest, raw_request: Request) -> WorkflowResponse:
    manager = get_workflow_manager(require_uid(raw_request))
    workflow = await manager.create_workflow(
        name=request.name,
        description=request.description,
        trigger=request.trigger,
        nodes=[node.model_dump() for node in request.nodes],
        edges=[edge.model_dump() for edge in request.edges],
        tags=request.tags,
    )
    return WorkflowResponse(**workflow.to_dict())


@router.get("", response_model=List[WorkflowResponse])
async def list_workflows(raw_request: Request) -> List[WorkflowResponse]:
    manager = get_workflow_manager(require_uid(raw_request))
    workflows = await manager.list_workflows()
    return [WorkflowResponse(**wf.to_dict()) for wf in workflows]


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: str, raw_request: Request) -> WorkflowResponse:
    manager = get_workflow_manager(require_uid(raw_request))
    workflow = await manager.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    return WorkflowResponse(**workflow.to_dict())


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str, raw_request: Request) -> Dict[str, str]:
    manager = get_workflow_manager(require_uid(raw_request))
    deleted = await manager.delete_workflow(workflow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    return {"status": "deleted", "workflow_id": workflow_id}


@router.post("/{workflow_id}/run", response_model=RunWorkflowResponse)
async def run_workflow(
    workflow_id: str,
    request: RunWorkflowRequest,
    raw_request: Request,
) -> RunWorkflowResponse:
    manager = get_workflow_manager(require_uid(raw_request))
    try:
        run = await manager.run_workflow(workflow_id, initial_context=request.context)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return RunWorkflowResponse(
        run_id=run.run_id,
        workflow_id=run.workflow_id,
        status=run.status.value,
        started_at=run.started_at,
        finished_at=run.finished_at,
        node_status=run.node_status,
        context=run.context,
        error=run.error,
    )


@router.get("/{workflow_id}/history")
async def workflow_history(workflow_id: str, raw_request: Request, limit: int = 20) -> List[Dict[str, Any]]:
    manager = get_workflow_manager(require_uid(raw_request))
    return await manager.get_run_history(workflow_id, limit=limit)

