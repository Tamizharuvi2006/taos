"""
TAOS Tool Executor — Executes tool calls with governance enforcement.

Production features:
- Timeout enforcement per tool call
- Rate limit checking before execution
- Policy validation (roles, max calls)
- Cost tracking
- Structured result format (PRD §6.3)
- Error classification
"""

from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any, Dict, Optional

from taos.config.constants import ErrorType
from taos.core.state.state_schema import StepResult
from taos.core.tools.registry import (
    ToolNotFoundError,
    ToolPolicyViolation,
    ToolRateLimitError,
    ToolRegistry,
)


class ToolExecutionError(Exception):
    """Raised when tool execution fails."""

    def __init__(self, message: str, error_type: str = ErrorType.TOOL_FAILURE):
        self.error_type = error_type
        super().__init__(message)


class ToolExecutor:
    """
    Production tool executor with full governance.
    
    Enforces rate limits, policies, timeouts, and produces
    structured results for every tool call.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        tool_name: str,
        tool_input: Optional[Dict[str, Any]] = None,
        task_id: str = "",
        step_id: str = "",
    ) -> StepResult:
        """
        Execute a tool call with full governance.
        
        Returns a StepResult with success/failure, latency, cost, and result data.
        """
        start_time = time.time()
        tool_input = tool_input or {}

        try:
            # ─── Get tool definition ───
            tool = self._registry.get(tool_name)

            # ─── Check policy ───
            violations = self._registry.check_policy(tool_name, task_id)
            if violations:
                raise ToolPolicyViolation(f"Policy violation: {'; '.join(violations)}")

            # ─── Check rate limit ───
            if not self._registry.check_rate_limit(tool_name):
                raise ToolRateLimitError(f"Rate limit exceeded for tool '{tool_name}'")

            # ─── Execute with timeout ───
            result = await asyncio.wait_for(
                tool.handler(**tool_input),
                timeout=tool.timeout,
            )

            # ─── Track call ───
            self._registry.increment_call_count(tool_name, task_id)

            latency = time.time() - start_time
            return StepResult(
                step_id=step_id,
                result=result,
                success=True,
                latency=latency,
                cost=tool.cost_estimate,
                tool_name=tool_name,
            )

        except ToolNotFoundError as e:
            return self._error_result(step_id, tool_name, str(e), ErrorType.TOOL_FAILURE, start_time)

        except ToolPolicyViolation as e:
            return self._error_result(step_id, tool_name, str(e), ErrorType.TOOL_AUTH_FAILURE, start_time)

        except ToolRateLimitError as e:
            return self._error_result(step_id, tool_name, str(e), ErrorType.TOOL_RATE_LIMIT, start_time)

        except asyncio.TimeoutError:
            return self._error_result(
                step_id, tool_name,
                f"Tool '{tool_name}' timed out after {self._registry.get(tool_name).timeout}s",
                ErrorType.TIMEOUT, start_time,
            )

        except Exception as e:
            return self._error_result(
                step_id, tool_name,
                f"Tool '{tool_name}' failed: {str(e)}\n{traceback.format_exc()}",
                ErrorType.TOOL_FAILURE, start_time,
            )

    def _error_result(
        self,
        step_id: str,
        tool_name: str,
        error: str,
        error_type: str,
        start_time: float,
    ) -> StepResult:
        """Create a standardized error StepResult."""
        return StepResult(
            step_id=step_id,
            result=None,
            success=False,
            error=error,
            error_type=error_type,
            latency=time.time() - start_time,
            cost=0.0,
            tool_name=tool_name,
        )
