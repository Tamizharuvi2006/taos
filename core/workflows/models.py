"""Workflow models for automation DAG execution."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    FAILED = "failed"


class WorkflowRunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class WorkflowNode:
    id: str
    type: str
    config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "type": self.type, "config": self.config}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowNode":
        return cls(id=data["id"], type=data["type"], config=data.get("config", {}))


@dataclass
class WorkflowEdge:
    from_node: str
    to_node: str
    condition: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"from_node": self.from_node, "to_node": self.to_node, "condition": self.condition}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowEdge":
        return cls(
            from_node=data["from_node"],
            to_node=data["to_node"],
            condition=data.get("condition"),
        )


@dataclass
class Workflow:
    workflow_id: str = ""
    name: str = ""
    description: str = ""
    status: WorkflowStatus = WorkflowStatus.DRAFT
    trigger: Dict[str, Any] = field(default_factory=dict)
    nodes: List[WorkflowNode] = field(default_factory=list)
    edges: List[WorkflowEdge] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    tags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.workflow_id:
            self.workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "trigger": self.trigger,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Workflow":
        return cls(
            workflow_id=data.get("workflow_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            status=WorkflowStatus(data.get("status", WorkflowStatus.DRAFT.value)),
            trigger=data.get("trigger", {}),
            nodes=[WorkflowNode.from_dict(n) for n in data.get("nodes", [])],
            edges=[WorkflowEdge.from_dict(e) for e in data.get("edges", [])],
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            tags=data.get("tags", []),
        )


@dataclass
class WorkflowRun:
    run_id: str = ""
    workflow_id: str = ""
    user_id: str = ""
    status: WorkflowRunStatus = WorkflowRunStatus.RUNNING
    started_at: float = 0.0
    finished_at: float = 0.0
    context: Dict[str, Any] = field(default_factory=dict)
    node_status: Dict[str, str] = field(default_factory=dict)
    error: str = ""

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = f"wfr_{uuid.uuid4().hex[:12]}"
        if not self.started_at:
            self.started_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "user_id": self.user_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "context": self.context,
            "node_status": self.node_status,
            "error": self.error,
        }
