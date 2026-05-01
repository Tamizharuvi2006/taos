"""
TAOS Global State Schema — The canonical state representation.

Every component reads state; ONLY the Controller mutates it.
State is versioned with hash-chaining for integrity verification.
"""

from __future__ import annotations

import hashlib
import json
import time
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, model_validator


# ═══════════════════════════════════════════════════════════
# PLAN SCHEMA
# ═══════════════════════════════════════════════════════════

class RetryPolicy(BaseModel):
    """Per-step retry configuration."""

    max_retries: int = 3
    backoff: str = "exponential"
    retry_on: List[str] = Field(default_factory=lambda: ["TOOL_FAILURE", "TIMEOUT"])


class StepType(str, Enum):
    """Execution step type."""

    TOOL = "tool"
    REASON = "reason"
    DAG_EXEC = "dag_exec"


class PlanStep(BaseModel):
    """A single step in an execution plan."""

    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    step_type: StepType = StepType.TOOL
    action: str
    tool: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    dag_name: Optional[str] = None
    dag_input_template: Optional[Dict[str, Any]] = None
    depends_on: List[str] = Field(default_factory=list)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    description: str = ""

    @model_validator(mode="after")
    def normalize_step_type(self) -> "PlanStep":
        raw_tool = (self.tool or "").strip().lower()
        explicit_dag_step = self.step_type == StepType.DAG_EXEC
        if explicit_dag_step or self.dag_name or raw_tool == "dag_exec":
            self.step_type = StepType.DAG_EXEC
            # DAG execution is handled by StepRunner, not ToolExecutor directly.
            if raw_tool == "dag_exec":
                self.tool = None
            if self.dag_input_template is None and isinstance(self.tool_input, dict):
                self.dag_input_template = dict(self.tool_input)
            if self.dag_input_template is None:
                self.dag_input_template = {}
            return self

        if self.tool:
            self.step_type = StepType.TOOL
        else:
            self.step_type = StepType.REASON
        return self


class PlanObject(BaseModel):
    """LLM-generated execution plan."""

    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    steps: List[PlanStep] = Field(default_factory=list)
    max_steps: int = 15
    cost_budget: float = 1.0
    created_at: float = Field(default_factory=time.time)

    @property
    def step_count(self) -> int:
        return len(self.steps)


# ═══════════════════════════════════════════════════════════
# STEP RESULT
# ═══════════════════════════════════════════════════════════

class StepResult(BaseModel):
    """Result of executing a single plan step."""

    step_id: str
    result: Any = None
    success: bool = False
    error: Optional[str] = None
    error_type: Optional[str] = None
    latency: float = 0.0
    cost: float = 0.0
    tool_name: Optional[str] = None
    retries_used: int = 0
    timestamp: float = Field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════
# REFLECTION RESULT
# ═══════════════════════════════════════════════════════════

class ReflectionResult(BaseModel):
    """Output from the reflection engine."""

    success: bool
    confidence: float = Field(ge=0.0, le=1.0)
    error_type: Optional[str] = None
    retry_recommended: bool = False
    reasoning: str = ""
    suggestions: List[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════
# COST BREAKDOWN (PRD §16)
# ═══════════════════════════════════════════════════════════

class CostBreakdown(BaseModel):
    """Detailed cost tracking per category."""

    planner_cost: float = 0.0
    execution_cost: float = 0.0
    tool_cost: float = 0.0
    reflection_cost: float = 0.0

    @property
    def total(self) -> float:
        return self.planner_cost + self.execution_cost + self.tool_cost + self.reflection_cost


# ═══════════════════════════════════════════════════════════
# GLOBAL STATE (PRD §5)
# ═══════════════════════════════════════════════════════════

class GlobalState(BaseModel):
    """
    The canonical TAOS global state.
    
    Rules (enforced by StateManager):
    1. ONLY the Controller mutates state
    2. Execution returns deltas only
    3. State Manager validates all updates
    4. All transitions are logged
    5. State is versioned with hash chaining
    """

    # ─── Identity ───
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    
    # ─── Core ───
    goal: str = ""
    step: int = 0
    current_fsm_state: str = "INIT"
    plan: Optional[PlanObject] = None
    
    # ─── Context & Memory ───
    context: List[str] = Field(default_factory=list)
    memory_refs: List[str] = Field(default_factory=list)
    
    # ─── Results ───
    tool_results: List[Dict[str, Any]] = Field(default_factory=list)
    step_results: List[StepResult] = Field(default_factory=list)
    
    # ─── Metrics ───
    cost: float = 0.0
    cost_breakdown: CostBreakdown = Field(default_factory=CostBreakdown)
    confidence: float = 1.0
    
    # ─── Status ───
    status: Literal["pending", "running", "success", "failed", "timeout", "cancelled"] = "pending"
    error: Optional[str] = None
    
    # ─── Versioning ───
    state_version: int = 0
    prev_state_hash: str = ""
    
    # ─── Timing ───
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    
    # ─── Replanning ───
    replan_count: int = 0

    def compute_hash(self) -> str:
        """Compute deterministic hash of current state for chain integrity."""
        state_dict = self.model_dump(exclude={"prev_state_hash", "updated_at"})
        state_json = json.dumps(state_dict, sort_keys=True, default=str)
        return hashlib.sha256(state_json.encode()).hexdigest()[:16]

    def model_copy_with_version(self, **updates) -> "GlobalState":
        """Create a new state version with updates applied."""
        new_state = self.model_copy(update={
            **updates,
            "state_version": self.state_version + 1,
            "prev_state_hash": self.compute_hash(),
            "updated_at": time.time(),
        })
        return new_state


# ═══════════════════════════════════════════════════════════
# STATE DELTA
# ═══════════════════════════════════════════════════════════

class StateDelta(BaseModel):
    """
    Represents a proposed change to the global state.
    Execution components return deltas — only the Controller applies them.
    """

    step_increment: int = 0
    confidence_update: Optional[float] = None
    cost_increment: float = 0.0
    new_tool_result: Optional[Dict[str, Any]] = None
    new_step_result: Optional[StepResult] = None
    new_context: Optional[str] = None
    new_memory_ref: Optional[str] = None
    status_update: Optional[str] = None
    error_update: Optional[str] = None
    fsm_state_update: Optional[str] = None
    plan_update: Optional[PlanObject] = None
    cost_breakdown_updates: Optional[Dict[str, float]] = None
    replan_increment: int = 0
