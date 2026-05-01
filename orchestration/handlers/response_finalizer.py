from __future__ import annotations

from typing import Any, Dict, Optional

from taos.orchestration.route_dispatcher import RouteExecutionContext, RouteExecutionResult


async def finalize_payload(
    context: RouteExecutionContext,
    *,
    raw_result: str,
    owner: str,
    route: str,
    state: Any = None,
) -> RouteExecutionResult:
    engine = context.engine
    payload: Optional[Dict[str, Any]] = await engine._finalize(
        state=state,
        classification=context.classification,
        raw_result=raw_result,
        goal_override=context.raw_query,
        user_id=context.user_id or "default",
    )
    payload = payload or {}
    return RouteExecutionResult(
        answer=str(payload.get("answer") or payload.get("formatted_response") or payload.get("result") or raw_result or ""),
        route=str(payload.get("route") or payload.get("route_label") or route or ""),
        owner=owner,
        confidence=float(payload.get("confidence") or 0.0),
        sources=list(payload.get("sources") or []),
        trust=dict(payload.get("trust_block") or {}),
        trace=dict(payload.get("trace") or {}),
        warnings=[str(item) for item in list(payload.get("warnings") or [])],
        metadata=dict(payload.get("metadata") or {}),
        payload=payload,
    )


def empty_result(owner: str, route: str = "") -> RouteExecutionResult:
    return RouteExecutionResult(owner=owner, route=route, payload=None)
