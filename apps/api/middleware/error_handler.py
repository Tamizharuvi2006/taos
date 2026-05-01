"""
TAOS API Middleware — Global error handler.

Catches unhandled exceptions and returns structured JSON error responses.
"""

from __future__ import annotations

import traceback
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from taos.apps.api.errors import build_error
from taos.config.settings import get_settings
from taos.core.security import redact_secret_text
from taos.infra.logging.logger import TAOSLogger


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    Global error handler middleware.

    Catches all unhandled exceptions and returns a structured
    JSON error response. In development, includes traceback;
    in production, returns a generic error message.
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        self._logger = TAOSLogger(name="taos.error")
        self._settings = get_settings()

    async def dispatch(self, request: Request, call_next: Callable):
        try:
            return await call_next(request)
        except Exception as e:
            self._logger.error(
                "unhandled_exception",
                error=redact_secret_text(e),
                path=str(request.url.path),
                method=request.method,
                traceback=redact_secret_text(traceback.format_exc()),
            )

            # Build error response
            request_id = request.headers.get("X-Request-ID", "unknown")
            error_body = build_error(
                code="INTERNAL_SERVER_ERROR",
                message="Internal server error",
                request_id=request_id,
            )
            error_body["error"] = "Request failed"
            error_body["code"] = "internal_error"

            # In development, include the actual error
            if self._settings.is_development:
                error_body["detail"] = redact_secret_text(e)  # type: ignore[index]
                error_body["traceback"] = redact_secret_text(traceback.format_exc())  # type: ignore[index]

            return JSONResponse(
                status_code=500,
                content=error_body,
            )
