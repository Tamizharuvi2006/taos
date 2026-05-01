"""
TAOS Constants — All enums, error types, and system-wide constants.
Single source of truth for magic strings and thresholds.
"""

from __future__ import annotations

from enum import Enum, unique


# ═══════════════════════════════════════════════════════════
# ERROR TYPES (PRD §31)
# ═══════════════════════════════════════════════════════════

@unique
class ErrorType(str, Enum):
    """All possible error classifications in TAOS."""

    TOOL_FAILURE = "TOOL_FAILURE"
    PLANNER_ERROR = "PLANNER_ERROR"
    TIMEOUT = "TIMEOUT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    TOOL_AUTH_FAILURE = "TOOL_AUTH_FAILURE"
    TOOL_RATE_LIMIT = "TOOL_RATE_LIMIT"
    MODEL_HALLUCINATION = "MODEL_HALLUCINATION"
    MEMORY_MISS = "MEMORY_MISS"
    COST_EXCEEDED = "COST_EXCEEDED"
    LOOP_DETECTED = "LOOP_DETECTED"
    STATE_CORRUPTION = "STATE_CORRUPTION"
    UNKNOWN = "UNKNOWN"


# ═══════════════════════════════════════════════════════════
# FSM STATES (PRD §6.2)
# ═══════════════════════════════════════════════════════════

@unique
class FSMState(str, Enum):
    """Controller finite state machine states."""

    INIT = "INIT"
    PLANNING = "PLANNING"
    PLAN_READY = "PLAN_READY"
    EXECUTING = "EXECUTING"
    REFLECTING = "REFLECTING"
    REPLANNING = "REPLANNING"
    TERMINATING = "TERMINATING"
    TERMINATED = "TERMINATED"
    FAILED = "FAILED"


# ═══════════════════════════════════════════════════════════
# TASK STATUS
# ═══════════════════════════════════════════════════════════

@unique
class TaskStatus(str, Enum):
    """Task-level status values."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


# ═══════════════════════════════════════════════════════════
# TASK PRIORITY (PRD §41)
# ═══════════════════════════════════════════════════════════

@unique
class TaskPriority(str, Enum):
    """Task priority levels for queue ordering."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ═══════════════════════════════════════════════════════════
# TOOL RISK LEVELS (PRD §7)
# ═══════════════════════════════════════════════════════════

@unique
class ToolRiskLevel(str, Enum):
    """Risk classification for tool governance."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ═══════════════════════════════════════════════════════════
# GOAL COMPLEXITY (PRD §15)
# ═══════════════════════════════════════════════════════════

@unique
class GoalComplexity(str, Enum):
    """Goal complexity classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ═══════════════════════════════════════════════════════════
# EXECUTION MODE (PRD §38)
# ═══════════════════════════════════════════════════════════

@unique
class ExecutionMode(str, Enum):
    """Execution concurrency mode."""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


# ═══════════════════════════════════════════════════════════
# CONTEXT COMPRESSION (PRD §12)
# ═══════════════════════════════════════════════════════════

@unique  
class CompressionStrategy(str, Enum):
    """Context window compression strategies."""

    SUMMARIZE = "summarize"
    DROP_OLD = "drop_old"
    HYBRID = "hybrid"


# ═══════════════════════════════════════════════════════════
# RETRYABLE ERRORS (PRD §14)
# ═══════════════════════════════════════════════════════════

RETRYABLE_ERRORS = frozenset({
    ErrorType.TOOL_FAILURE,
    ErrorType.TIMEOUT,
    ErrorType.TOOL_RATE_LIMIT,
    ErrorType.MEMORY_MISS,
})

NON_RETRYABLE_ERRORS = frozenset({
    ErrorType.VALIDATION_ERROR,
    ErrorType.TOOL_AUTH_FAILURE,
    ErrorType.STATE_CORRUPTION,
    ErrorType.COST_EXCEEDED,
})


# ═══════════════════════════════════════════════════════════
# SYSTEM DEFAULTS
# ═══════════════════════════════════════════════════════════

DEFAULT_MAX_TOKENS_CONTEXT = 8192
DEFAULT_SUMMARIZATION_TRIGGER = 6144
DEFAULT_MEMORY_MIN_CONFIDENCE = 0.7
DEFAULT_RELEVANCE_WEIGHT = 0.7
DEFAULT_RECENCY_WEIGHT = 0.3
