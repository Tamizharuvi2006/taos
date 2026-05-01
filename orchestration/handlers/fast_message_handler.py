from __future__ import annotations

from taos.orchestration.handlers.response_finalizer import empty_result
from taos.orchestration.route_dispatcher import RouteExecutionContext, RouteExecutionResult


class FastMessageHandler:
    async def handle(self, context: RouteExecutionContext) -> RouteExecutionResult:
        # Fast-message execution is still handled by the existing micro-fast path
        # before owner dispatch. Returning empty preserves current fall-through behavior.
        return empty_result("direct_fast_message", "fast_message")
