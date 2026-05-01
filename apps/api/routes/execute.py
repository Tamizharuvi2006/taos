"""
TAOS API — Execute endpoint.

The primary API endpoint for running agent tasks.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from taos.apps.api.auth_context import resolve_user_id
from taos.apps.api.errors import raise_api_error
from taos.apps.api.schemas.request import ExecuteRequest, PlanRequest, BatchRequest
from taos.apps.api.schemas.response import ExecuteResponse, PlanResponse, ErrorResponse
from taos.config.settings import get_settings
from taos.core.limits import get_quota_manager
from taos.core.performance.progress import register_tracker_owner
from taos.core.routing.route_rules import deterministic_route, safe_default_route
from taos.infra.logging.logger import TAOSLogger
from taos.orchestration.engine import OrchestrationEngine
from taos.orchestration.route_dispatcher import RouteDispatcher
from taos.orchestration.workflow import WorkflowRunner

router = APIRouter(tags=["execute"])
_ROUTE_DISPATCHER = RouteDispatcher()


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _quota_route_owner_hint(goal: str, *, doc_context_active: bool) -> tuple[str, str]:
    normalized = str(goal or "").strip().lower()
    context = {"has_active_doc": bool(doc_context_active)}
    decision = deterministic_route(normalized, context)
    if decision is None:
        decision = safe_default_route(normalized, context)
    route = str(getattr(decision, "route", "") or "").strip()
    owner = _ROUTE_DISPATCHER.owner_for_route(route) if route else ""
    return route, owner


@router.post(
    "/execute",
    response_model=ExecuteResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        500: {"model": ErrorResponse, "description": "Internal error"},
    },
)
async def execute_task(request: ExecuteRequest, raw_request: Request) -> ExecuteResponse:
    """
    Execute an agent task.

    Takes a goal string and runs the full PLAN → EXECUTE → REFLECT pipeline.
    Returns the result with step-by-step execution details.
    """
    user_id = resolve_user_id(raw_request, request.user_id)
    request_id = request.request_id or raw_request.headers.get("X-Request-ID", "unknown")
    logger = TAOSLogger(name="taos.api", request_id=request_id)
    settings = get_settings()

    try:
        register_tracker_owner(request_id, user_id)
        route_hint, owner_hint = _quota_route_owner_hint(request.goal, doc_context_active=bool(request.doc_context_active))
        quota_decision = get_quota_manager().check_and_consume_detailed(
            user_id=user_id,
            tier=request.user_tier,
            route=route_hint,
            owner=owner_hint,
            perf_test_mode_requested=(
                _is_truthy(raw_request.headers.get("X-Perf-Test-Mode"))
                or _is_truthy(raw_request.query_params.get("perf_test_mode"))
                or bool(settings.perf_test_mode)
            ),
        )
        if not quota_decision.allowed:
            raise_api_error(
                429,
                quota_decision.code or "RATE_LIMITED",
                quota_decision.reason or "Rate limit exceeded",
                request_id,
                route=quota_decision.route or route_hint,
                owner=quota_decision.owner or owner_hint,
                retry_after_seconds=quota_decision.retry_after_seconds,
                extra={
                    "limit_per_minute": quota_decision.limit_per_minute,
                    "daily_quota": quota_decision.daily_quota,
                    "effective_tier": quota_decision.effective_tier,
                    "perf_mode_applied": quota_decision.perf_mode_applied,
                },
            )

        engine = OrchestrationEngine(logger=logger)
        result = await engine.run(
            goal=request.goal,
            request_id=request_id,
            user_id=user_id,
            doc_context_active=bool(request.doc_context_active),
        )

        return ExecuteResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("api.execute_error", error=str(e))
        raise_api_error(500, "EXECUTION_ERROR", str(e), request_id)


@router.post(
    "/plan",
    response_model=PlanResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        500: {"model": ErrorResponse, "description": "Internal error"},
    },
)
async def generate_plan(request: PlanRequest, raw_request: Request) -> PlanResponse:
    """
    Generate an execution plan without running it.

    Useful for previewing what the agent would do before execution.
    """
    request_id = raw_request.headers.get("X-Request-ID", "unknown")
    _ = resolve_user_id(raw_request, None)
    logger = TAOSLogger(name="taos.api", request_id=request_id)

    try:
        engine = OrchestrationEngine(logger=logger)
        workflow = WorkflowRunner(engine)
        result = await workflow.run_plan_only(request.goal)

        if not result.success:
            raise HTTPException(status_code=400, detail=result.error)

        return PlanResponse(**result.result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("api.plan_error", error=str(e))
        raise_api_error(500, "PLAN_ERROR", str(e), request_id)


@router.post("/batch")
async def execute_batch(request: BatchRequest, raw_request: Request):
    """Execute multiple goals sequentially."""
    request_id = raw_request.headers.get("X-Request-ID", "unknown")
    _ = resolve_user_id(raw_request, None)
    logger = TAOSLogger(name="taos.api", request_id=request_id)

    try:
        engine = OrchestrationEngine(logger=logger)
        workflow = WorkflowRunner(engine)
        results = await workflow.run_batch(
            goals=request.goals,
            stop_on_failure=request.stop_on_failure,
        )

        return {
            "total": len(results),
            "succeeded": sum(1 for r in results if r.success),
            "failed": sum(1 for r in results if not r.success),
            "results": [
                {
                    "success": r.success,
                    "result": r.result,
                    "error": r.error,
                    "elapsed_time": r.elapsed_time,
                }
                for r in results
            ],
        }

    except Exception as e:
        logger.error("api.batch_error", error=str(e))
        raise_api_error(500, "BATCH_ERROR", str(e), request_id)
