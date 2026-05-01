"""TAOS API - Feedback routes for self-learning memory."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from taos.apps.api.auth_context import resolve_user_id
from taos.apps.api.errors import raise_api_error
from taos.apps.api.schemas.feedback import FeedbackRequest, FeedbackResponse, FeedbackSignalRequest, FeedbackSignalResponse
from taos.core.feedback import GLOBAL_FEEDBACK_STORE, UserFeedback
from taos.infra.logging.logger import TAOSLogger
from taos.orchestration.engine import OrchestrationEngine

router = APIRouter(tags=["feedback"])


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    summary="Store explicit user correction feedback",
)
async def submit_feedback(request: FeedbackRequest, raw_request: Request) -> FeedbackResponse:
    request_id = raw_request.headers.get("X-Request-ID", "unknown")
    user_id = resolve_user_id(raw_request, request.user_id)
    logger = TAOSLogger(name="taos.feedback", request_id=request_id)

    try:
        engine = OrchestrationEngine(logger=logger)
        feedback_id = await engine.record_feedback(
            user_id=user_id,
            query=request.query,
            bad_answer=request.bad_answer,
            corrected_answer=request.corrected_answer,
            tags=request.tags,
            rating=request.rating,
        )
        return FeedbackResponse(success=True, feedback_id=feedback_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("feedback.submit_error", error=str(e))
        raise_api_error(500, "FEEDBACK_ERROR", str(e), request_id)


@router.post(
    "/feedback/signal",
    response_model=FeedbackSignalResponse,
    summary="Store user answer-quality feedback for review",
)
async def submit_feedback_signal(request: FeedbackSignalRequest, raw_request: Request) -> FeedbackSignalResponse:
    request_id = raw_request.headers.get("X-Request-ID", "unknown")
    user_id = resolve_user_id(raw_request, request.user_id)
    try:
        feedback_id = GLOBAL_FEEDBACK_STORE.add(
            UserFeedback(
                user_id=user_id,
                query=request.query,
                feedback_type=request.feedback_type,
                answer=request.answer,
                route=request.route,
                answer_mode=request.answer_mode,
                source_quality=request.source_quality,
                notes=request.notes,
            )
        )
        return FeedbackSignalResponse(feedback_id=feedback_id)
    except ValueError as e:
        raise_api_error(400, "FEEDBACK_VALIDATION_ERROR", str(e), request_id)
