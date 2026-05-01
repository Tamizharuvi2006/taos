from __future__ import annotations

from taos.orchestration.handlers.response_finalizer import finalize_payload
from taos.orchestration.route_dispatcher import RouteExecutionContext, RouteExecutionResult


class ResearchHandler:
    async def handle(self, context: RouteExecutionContext) -> RouteExecutionResult:
        engine = context.engine
        engine._log("engine.deep_research_pipeline", query=context.query)
        universal = dict((context.metadata or {}).get("universal_understanding") or {})
        if universal:
            engine._trace_data.setdefault("evidence_stats", {})["query_plan_summary"] = dict(
                universal.get("query_plan_summary") or {}
            )
            if universal.get("meaning_frame"):
                engine._trace_data["meaning_frame"] = dict(universal.get("meaning_frame") or {})
        if engine._is_package_version_lookup(context.raw_query):
            await self._preempt_package_lookup(context)
            fast_search_answer = await engine._run_fast_search(context.raw_query)
            if fast_search_answer and not engine._should_escalate_fast_search():
                return await finalize_payload(
                    context,
                    raw_result=fast_search_answer,
                    owner="search_lite",
                    route="fast_search",
                )
        engine._trace_data["planner_path"] = "deep_research"
        engine._trace_data["dag_name"] = "research_v2"
        engine._set_trace_value("planner_path", "deep_research")
        engine._set_trace_value("dag_name", "research_v2")
        deep_res = await engine._run_deep_research(context.query)
        if deep_res:
            return await finalize_payload(
                context,
                raw_result=deep_res,
                owner="research_pipeline",
                route=str((context.route_decision or {}).get("route") or "deep_search"),
            )
        return await finalize_payload(
            context,
            raw_result=(
                "I could not verify a reliable current-status update from live sources right now. "
                "Please retry in a moment and I will return the latest verifiable update with source timing."
            ),
            owner="research_pipeline",
            route=str((context.route_decision or {}).get("route") or "deep_search"),
        )

    async def _preempt_package_lookup(self, context: RouteExecutionContext) -> None:
        engine = context.engine
        classification = context.classification
        if classification and isinstance(classification.metadata, dict):
            classification.metadata["route_label"] = "fast_search"
            route_decision = classification.metadata.get("route_decision")
            if isinstance(route_decision, dict):
                route_decision.update(
                    {
                        "route": "fast_search",
                        "selected_route": "fast_search",
                        "phase107_route": "fast_search",
                        "route_owner": "search_lite",
                        "route_reason": "source_of_record_package_version_preempt",
                        "policy_reason": "source_of_record_package_version_preempt",
                    }
                )
            boundary_summary = classification.metadata.get("route_boundary_summary")
            if isinstance(boundary_summary, dict):
                boundary_summary.update(
                    {
                        "route": "fast_search",
                        "selected_route": "fast_search",
                        "owner": "search_lite",
                        "research_allowed": False,
                        "web_search_allowed": True,
                    }
                )
        engine._trace_data["planner_path"] = "fast_search"
        engine._trace_data["dag_name"] = "search_lite"
        engine._trace_data["route_label"] = "fast_search"
        engine._set_trace_value("planner_path", "fast_search")
        engine._set_trace_value("dag_name", "search_lite")
        engine._set_trace_value("route_label", "fast_search")
