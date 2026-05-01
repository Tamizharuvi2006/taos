"""
Fallback builders used by execute endpoints for reliable degradation.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from taos.apps.api.response_contract import build_clarification_payload, build_timeout_payload


class TimeoutFallbackBuilder:
    def build(
        self,
        *,
        request_id: str,
        elapsed_ms: float,
        partial_result: Optional[str] = None,
        route: str = "standard_task",
        owner: str = "",
        timeout_stage: Optional[str] = None,
        budget_stage: Optional[str] = None,
        partial_answer_used: Optional[bool] = None,
        first_event_latency_ms: Optional[float] = None,
        streaming_started_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        return build_timeout_payload(
            request_id=request_id,
            elapsed_ms=elapsed_ms,
            partial_result=partial_result,
            route=route,
            owner=owner,
            timeout_stage=timeout_stage,
            budget_stage=budget_stage,
            partial_answer_used=partial_answer_used,
            first_event_latency_ms=first_event_latency_ms,
            streaming_started_at=streaming_started_at,
        )


class AmbiguityFallbackHandler:
    _AMBIGUOUS_QUERY = re.compile(
        r"^(this|that|it|older one|same one|that one|this one|continue this|explain this|more on this)$",
        re.I,
    )

    def should_clarify(self, query: str, *, has_context: bool) -> bool:
        text = " ".join(str(query or "").strip().lower().split())
        if not text or has_context:
            return False
        if len(text.split()) <= 3 and self._AMBIGUOUS_QUERY.search(text):
            return True
        return False

    def build(self, *, request_id: str, query: str) -> Dict[str, Any]:
        return build_clarification_payload(request_id=request_id, query=query)
