"""
TAOS Tool Registry — Governed tool registration and lookup.

Production features:
- Schema-validated tool registration
- Rate limiting per tool
- Risk level classification
- Permission-based access control
- Cost estimation per tool call
- Tool policy enforcement (PRD §7)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

from taos.config.constants import ToolRiskLevel
from taos.config.settings import get_settings


@dataclass
class ToolPolicy:
    """Governance policy for a tool (PRD §7)."""

    allowed_roles: List[str] = field(default_factory=lambda: ["agent"])
    max_calls_per_task: int = 50
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    audit_required: bool = False


@dataclass
class ToolDefinition:
    """Complete tool definition with schema, policy, and handler."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[..., Coroutine[Any, Any, Any]]
    policy: ToolPolicy = field(default_factory=ToolPolicy)
    rate_limit: int = 60  # calls per minute
    cost_estimate: float = 0.0
    timeout: int = 30
    tags: List[str] = field(default_factory=list)

    # Runtime tracking
    _call_count: int = field(default=0, repr=False)
    _last_reset: float = field(default_factory=time.time, repr=False)


class ToolNotFoundError(Exception):
    pass


class ToolPolicyViolation(Exception):
    pass


class ToolRateLimitError(Exception):
    pass


class ToolRegistry:
    """
    Production tool registry with governance.
    
    Manages tool registration, lookup, rate limiting,
    and policy enforcement. Only registered tools can be executed.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._task_call_counts: Dict[str, Dict[str, int]] = {}  # task_id -> {tool_name -> count}

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool. Overwrites if name already exists."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        """Get a registered tool by name."""
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' not registered. Available: {self.list_names()}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def list_names(self) -> List[str]:
        """List all registered tool names."""
        return sorted(self._tools.keys())

    def list_tools(self) -> List[ToolDefinition]:
        """List all registered tools."""
        return list(self._tools.values())

    def get_names_set(self) -> Set[str]:
        """Get tool names as a set (for validator)."""
        return set(self._tools.keys())

    def check_rate_limit(self, tool_name: str) -> bool:
        """Check if a tool is within its rate limit."""
        if get_settings().disable_tool_limits:
            return True
        tool = self.get(tool_name)
        now = time.time()

        # Reset counter every minute
        if now - tool._last_reset > 60:
            tool._call_count = 0
            tool._last_reset = now

        return tool._call_count < tool.rate_limit

    def increment_call_count(self, tool_name: str, task_id: str = "") -> None:
        """Track a tool call for rate limiting and policy enforcement."""
        tool = self.get(tool_name)
        tool._call_count += 1

        if task_id:
            if task_id not in self._task_call_counts:
                self._task_call_counts[task_id] = {}
            counts = self._task_call_counts[task_id]
            counts[tool_name] = counts.get(tool_name, 0) + 1

    def check_policy(self, tool_name: str, task_id: str = "", role: str = "agent") -> List[str]:
        """
        Check tool policy. Returns list of violations (empty = OK).
        """
        tool = self.get(tool_name)
        violations = []
        disable_limits = get_settings().disable_tool_limits

        # Check role permission
        if role not in tool.policy.allowed_roles:
            violations.append(f"Role '{role}' not allowed for tool '{tool_name}'")

        # Check per-task call limit
        if not disable_limits and task_id and task_id in self._task_call_counts:
            count = self._task_call_counts[task_id].get(tool_name, 0)
            if count >= tool.policy.max_calls_per_task:
                violations.append(
                    f"Tool '{tool_name}' exceeded max calls per task "
                    f"({count}/{tool.policy.max_calls_per_task})"
                )

        # Check rate limit
        if not disable_limits and not self.check_rate_limit(tool_name):
            violations.append(f"Tool '{tool_name}' rate limit exceeded")

        return violations

    def get_tool_descriptions(self) -> str:
        """Get formatted tool descriptions for LLM prompts."""
        lines = []
        for tool in self._tools.values():
            lines.append(f"- **{tool.name}**: {tool.description}")
            if tool.input_schema:
                params = ", ".join(f"{k}: {v}" for k, v in tool.input_schema.items())
                lines.append(f"  Input: {{{params}}}")
        return "\n".join(lines)

    def reset_task_counts(self, task_id: str) -> None:
        """Reset per-task call counts (on task completion)."""
        self._task_call_counts.pop(task_id, None)

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry."""
        self._tools.pop(name, None)
