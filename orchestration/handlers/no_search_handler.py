from __future__ import annotations

from taos.orchestration.handlers.response_finalizer import finalize_payload
from taos.orchestration.route_dispatcher import RouteExecutionContext, RouteExecutionResult


class NoSearchHandler:
    async def handle(self, context: RouteExecutionContext) -> RouteExecutionResult:
        engine = context.engine
        engine._log("engine.no_search_direct", query=context.query[:100])
        engine._trace_data["planner_path"] = "no_search"
        engine._set_trace_value("planner_path", "no_search")
        engine._append_direct_trace_step(
            step_type="reason",
            status="success",
            summary="Answered from direct model knowledge without planner or web search.",
        )
        direct_answer = await engine._run_fast_llm(
            "Answer directly without using tools or web search. "
            "If the question depends on current/latest information, say it needs verification instead.\n\n"
            f"User question: {context.query}",
            apply_tone=True,
            stream_to_progress=True,
        )
        return await finalize_payload(
            context,
            raw_result=direct_answer or engine._build_direct_knowledge_fallback(context.query),
            owner="direct_llm_no_tools",
            route="no_search",
        )
