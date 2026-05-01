from __future__ import annotations

from taos.orchestration.handlers.response_finalizer import empty_result
from taos.orchestration.route_dispatcher import RouteExecutionContext, RouteExecutionResult


class TaskHandler:
    async def handle(self, context: RouteExecutionContext) -> RouteExecutionResult:
        # Planner/FSM task execution remains in the main engine loop for Phase 109.
        # The explicit handler keeps route ownership visible without behavior changes.
        owner = str((context.route_decision or {}).get("route_owner") or "fsm_planner")
        route = str((context.route_decision or {}).get("route") or "task")
        return empty_result(owner, route)
