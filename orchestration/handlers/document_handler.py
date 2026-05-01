from __future__ import annotations

from taos.core.performance.progress import ProgressPhase
from taos.orchestration.handlers.response_finalizer import finalize_payload
from taos.orchestration.route_dispatcher import RouteExecutionContext, RouteExecutionResult


class DocumentHandler:
    async def handle(self, context: RouteExecutionContext) -> RouteExecutionResult:
        engine = context.engine
        engine._log(
            "engine.doc_mode_direct",
            source="route_owner_dispatch",
            doc_context_active=bool(context.doc_context_active),
            doc_count=len(context.doc_ids),
        )
        doc_answer, doc_rows, planner_path, doc_payload = await engine._resolve_doc_mode_direct_answer(
            goal=context.query,
            doc_context_active=bool(context.doc_context_active),
            doc_ids=context.doc_ids,
        )
        engine._trace_data["planner_path"] = planner_path
        engine._set_trace_value("planner_path", planner_path)
        doc_metadata = dict(doc_payload.get("metadata") or {})
        doc_warnings = [str(item).strip() for item in list(doc_payload.get("warnings") or []) if str(item).strip()]
        doc_summary = engine._build_doc_mode_summary(
            payload=doc_payload,
            source_rows=doc_rows,
            planner_path=planner_path,
        )
        engine._set_trace_value("document_summary", doc_summary)
        if doc_warnings:
            engine._set_trace_value("document_warnings", doc_warnings)
        stats = engine._trace_data.setdefault("evidence_stats", {})
        stats["source_rows"] = doc_rows
        stats["source_count"] = len(doc_rows)
        stats["doc_summary"] = doc_summary
        stats["provider_count"] = len(
            {
                str(row.get("provider") or "").strip().lower()
                for row in doc_rows
                if str(row.get("provider") or "").strip()
            }
        )
        stats["domain_diversity"] = 0.65 if planner_path == "doc_mode_retrieval" else (0.5 if context.doc_context_active else 0.25)
        stats["extraction_quality"] = 0.82 if planner_path == "doc_mode_retrieval" else (0.55 if context.doc_context_active else 0.35)
        if planner_path == "doc_mode_retrieval":
            stats["cache_summary"] = {
                **engine._document_cache_summary(cache_hit=bool(doc_metadata.get("cache_hit"))),
            }
            stats["evidence_selection_summary"] = {
                "selected_count": len(doc_rows),
                "selection_reason": "document_retrieval",
                "retrieval_strength": doc_summary.get("retrieval_strength"),
            }
            stats["citation_plan_summary"] = {
                "coverage": float(doc_summary.get("validation_score") or 0.0),
                "unsupported_claims": int(doc_summary.get("validation_issue_count") or 0),
                "source_count": len(doc_rows),
            }
        engine._append_direct_trace_step(
            step_type="reason",
            status="success",
            tool=("document_ask" if planner_path == "doc_mode_retrieval" else None),
            summary=(
                "Handled document-mode request using retrieval-grounded document ask service."
                if planner_path == "doc_mode_retrieval"
                else "Handled document-mode request through direct exam/doc response path."
            ),
        )
        if context.tracker:
            context.tracker.update(
                ProgressPhase.FORMATTING,
                detail=("DOC_MODE_RETRIEVAL" if planner_path == "doc_mode_retrieval" else "DOC_MODE_DIRECT"),
            )
        return await finalize_payload(
            context,
            raw_result=doc_answer,
            owner="document_pipeline",
            route="doc_mode",
        )
