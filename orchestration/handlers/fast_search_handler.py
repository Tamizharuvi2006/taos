from __future__ import annotations

from taos.core.performance.progress import ProgressPhase
from taos.orchestration.handlers.response_finalizer import finalize_payload
from taos.orchestration.route_dispatcher import RouteExecutionContext, RouteExecutionResult


class FastSearchHandler:
    async def handle(self, context: RouteExecutionContext) -> RouteExecutionResult:
        engine = context.engine
        engine._log("engine.fast_search_pipeline", query=context.query)
        engine._trace_data["planner_path"] = "fast_search"
        engine._trace_data["dag_name"] = "search_lite"
        engine._set_trace_value("planner_path", "fast_search")
        engine._set_trace_value("dag_name", "search_lite")
        fast_search_answer = await engine._run_fast_search(context.raw_query)
        if fast_search_answer and engine._should_escalate_fast_search():
            evidence = dict(engine._trace_data.get("evidence_stats") or {})
            fallback_route = "official_search" if bool(evidence.get("official_source_required")) else "deep_search"
            boundary_summary = dict(engine._trace_data.get("route_boundary_summary") or {})
            boundary_summary["fallback_owner"] = "research_pipeline"
            boundary_summary["fallback_reason"] = "fast_search_unverified"
            boundary_summary["research_allowed"] = True
            engine._set_trace_value("route_boundary_summary", boundary_summary)
            engine._append_direct_trace_step(
                step_type="reason",
                status="success",
                tool="research_v2",
                summary="Search Lite could not verify the fact strongly enough, so TAOS escalated to research verification.",
            )
            engine._trace_data["planner_path"] = "deep_research"
            engine._trace_data["dag_name"] = "research_v2"
            engine._set_trace_value("planner_path", "deep_research")
            engine._set_trace_value("dag_name", "research_v2")
            engine._set_trace_value("route_label", fallback_route)
            deep_res = await engine._run_deep_research(context.query)
            if deep_res:
                return await finalize_payload(
                    context,
                    raw_result=deep_res,
                    owner="research_pipeline",
                    route=fallback_route,
                )
        if fast_search_answer:
            return await finalize_payload(
                context,
                raw_result=fast_search_answer,
                owner="search_lite",
                route="fast_search",
            )
        return RouteExecutionResult(owner="search_lite", route="fast_search", payload=None)
