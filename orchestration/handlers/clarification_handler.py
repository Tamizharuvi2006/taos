from __future__ import annotations

from taos.orchestration.handlers.response_finalizer import finalize_payload
from taos.orchestration.route_dispatcher import RouteExecutionContext, RouteExecutionResult


class ClarificationHandler:
    async def handle(self, context: RouteExecutionContext) -> RouteExecutionResult:
        engine = context.engine
        engine._trace_data["planner_path"] = "clarification"
        engine._set_trace_value("planner_path", "clarification")
        engine._append_direct_trace_step(
            step_type="reason",
            status="success",
            summary="Returned clarification prompt because query is ambiguous without prior context.",
        )
        return await finalize_payload(
            context,
            raw_result=engine._build_ambiguous_clarification_response(context.query),
            owner="clarification_fallback",
            route="clarification",
        )
