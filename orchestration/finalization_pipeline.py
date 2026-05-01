from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from taos.core.research.research_pipeline import build_rumour_no_confirmation_answer
from taos.core.understanding import enforce_meaning_frame
from taos.core.state.state_schema import GlobalState


@dataclass
class FinalizationMetadata:
    response_keys: List[str] = field(default_factory=list)
    public_trace_present: bool = False
    warnings_count: int = 0


class FinalizationPipeline:
    """Named finalization boundary for the legacy response contract."""

    async def finalize(
        self,
        *,
        engine: Any,
        state: Optional[GlobalState],
        classification: Any = None,
        raw_result: Optional[str] = None,
        goal_override: Optional[str] = None,
        user_id: str = "default",
    ) -> Dict[str, Any]:
        result = await engine._finalize_legacy(
            state=state,
            classification=classification,
            raw_result=raw_result,
            goal_override=goal_override,
            user_id=user_id,
        )
        return self._apply_meaning_frame_authority(engine=engine, result=result)

    def _apply_meaning_frame_authority(self, *, engine: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(result or {})
        meaning_frame = (
            (payload.get("trace") or {}).get("meaning_frame")
            or (engine._trace_data or {}).get("meaning_frame")
            or ((engine._trace_data or {}).get("universal_understanding") or {}).get("meaning_frame")
        )
        original_answer = str(
            payload.get("answer")
            or payload.get("formatted_response")
            or payload.get("result")
            or ""
        ).strip()
        corrected_answer, drift_detected = enforce_meaning_frame(original_answer, meaning_frame)
        if not drift_detected:
            return payload

        evidence_rows = list((((payload.get("trace") or {}).get("evidence_stats") or {}).get("source_rows") or []))
        if not evidence_rows:
            evidence_rows = list((((engine._trace_data or {}).get("evidence_stats") or {}).get("source_rows") or []))
        if isinstance(meaning_frame, dict) and str(meaning_frame.get("user_intent") or "") == "rumour_verification":
            corrected_answer = build_rumour_no_confirmation_answer(
                query=str(meaning_frame.get("original_query") or meaning_frame.get("normalized_query") or ""),
                evidence_rows=evidence_rows,
                checked_queries=list((((engine._trace_data or {}).get("evidence_stats") or {}).get("query_plan_summary") or {}).get("primary_queries") or []),
            )

        payload["answer"] = corrected_answer
        payload["formatted_response"] = corrected_answer
        payload["result"] = corrected_answer
        payload["direct_answer"] = corrected_answer[:250] + ("..." if len(corrected_answer) > 250 else "")
        warnings = [str(item) for item in list(payload.get("warnings") or [])]
        warnings.append("Answer topic drift was blocked because it conflicted with the resolved user meaning.")
        payload["warnings"] = warnings
        trace = dict(payload.get("trace") or {})
        trace["subject_drift_detected"] = True
        if isinstance(meaning_frame, dict) and str(meaning_frame.get("user_intent") or "") == "rumour_verification":
            trace["exact_claim_status"] = "not_confirmed"
            trace["related_evidence_used"] = bool(evidence_rows)
            trace["related_evidence_sources_count"] = len(evidence_rows)
            trace["source_of_record_checked"] = any(
                "supported" in " ".join(str(row.get(key) or "") for key in ("title", "snippet", "summary")).lower()
                for row in evidence_rows
            )
            trace["confusion_explanation_present"] = "What may be causing confusion:" in corrected_answer
        if meaning_frame:
            trace["meaning_frame"] = meaning_frame
        payload["trace"] = trace
        metadata = dict(payload.get("metadata") or {})
        metadata["subject_drift_detected"] = True
        payload["metadata"] = metadata
        trust = dict(payload.get("trust_block") or {})
        if trust:
            trust["confidence"] = "Low"
            payload["trust_block"] = trust
        return payload

    def metadata_for(self, result: Dict[str, Any]) -> FinalizationMetadata:
        trace = result.get("trace") or result.get("execution_trace") or {}
        public_trace = trace.get("public_summary") if isinstance(trace, dict) else None
        warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
        return FinalizationMetadata(
            response_keys=sorted(str(key) for key in result.keys()),
            public_trace_present=bool(public_trace),
            warnings_count=len(warnings),
        )
