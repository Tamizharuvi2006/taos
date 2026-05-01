"""
TAOS Micro-DAG models used by FSM step execution.

DAG execution is a controlled primitive under FSM orchestration.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class DagNodeKind(str, Enum):
    """Supported node execution kinds."""

    TOOL = "tool"
    TRANSFORM = "transform"
    VALIDATE = "validate"
    LLM = "llm"


class DagNode(BaseModel):
    """A single node in a DAG definition."""

    id: str
    kind: DagNodeKind = DagNodeKind.TOOL
    action: str = ""
    tool: Optional[str] = None
    prompt_template: Optional[str] = None
    inputs: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    condition: Optional[str] = None
    retry_limit: int = 2
    timeout_seconds: int = 20
    required: bool = True

    @model_validator(mode="after")
    def validate_node(self) -> "DagNode":
        if self.kind == DagNodeKind.TOOL and not self.tool:
            raise ValueError(f"DAG node '{self.id}' is tool kind but has no tool")
        if self.retry_limit < 0:
            self.retry_limit = 0
        if self.timeout_seconds <= 0:
            self.timeout_seconds = 20
        if self.id in self.depends_on:
            raise ValueError(f"DAG node '{self.id}' cannot depend on itself")
        return self


class DagDefinition(BaseModel):
    """A DAG definition with dependency graph and outputs."""

    name: str
    version: str = "1.0.0"
    description: str = ""
    nodes: List[DagNode] = Field(default_factory=list)
    output_node_ids: List[str] = Field(default_factory=list)
    max_runtime_seconds: int = 120

    @model_validator(mode="after")
    def validate_definition(self) -> "DagDefinition":
        if not self.nodes:
            raise ValueError(f"DAG '{self.name}' must contain at least one node")

        node_ids = [n.id for n in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError(f"DAG '{self.name}' has duplicate node ids")

        valid = set(node_ids)
        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in valid:
                    raise ValueError(
                        f"DAG '{self.name}' node '{node.id}' depends on unknown node '{dep}'"
                    )

        if not self.output_node_ids:
            self.output_node_ids = [self.nodes[-1].id]
        else:
            for out in self.output_node_ids:
                if out not in valid:
                    raise ValueError(
                        f"DAG '{self.name}' references unknown output node '{out}'"
                    )

        if self.max_runtime_seconds <= 0:
            self.max_runtime_seconds = 120
        return self


class DagNodeResult(BaseModel):
    """Execution outcome for one DAG node."""

    node_id: str
    status: Literal["success", "failed", "skipped"] = "success"
    output: Any = None
    error: Optional[str] = None
    retries_used: int = 0
    latency: float = 0.0
    cost: float = 0.0
    tool_name: Optional[str] = None
    batch_index: int = 0
    frontier_index: int = 0


class DagRunResult(BaseModel):
    """Aggregated DAG run outcome."""

    dag_name: str
    status: Literal["success", "failed"] = "success"
    execution_mode: Literal["sequential", "parallel"] = "sequential"
    node_results: Dict[str, DagNodeResult] = Field(default_factory=dict)
    final_output: Dict[str, Any] = Field(default_factory=dict)
    batches: List[Dict[str, Any]] = Field(default_factory=list)
    frontier_count: int = 0
    total_cost: float = 0.0
    total_latency: float = 0.0
    error: Optional[str] = None
