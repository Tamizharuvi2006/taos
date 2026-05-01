"""
TAOS API Middleware — Request logging.

Logs every incoming request with timing, status code, and request ID.
"""

from __future__ import annotations

import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from taos.infra.logging.logger import TAOSLogger


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs all incoming HTTP requests with:
    - Request ID (generated or from header)
    - Method and path
    - Response status code
    - Latency in milliseconds
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        self._logger = TAOSLogger(name="taos.http")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Extract or generate request ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
        start_time = time.time()

        self._logger.info(
            "http.request",
            method=request.method,
            path=str(request.url.path),
            request_id=request_id,
        )

        try:
            response = await call_next(request)
            latency_ms = (time.time() - start_time) * 1000

            self._logger.info(
                "http.response",
                method=request.method,
                path=str(request.url.path),
                status_code=response.status_code,
                latency_ms=round(latency_ms, 2),
                request_id=request_id,
            )

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time"] = f"{latency_ms:.2f}ms"

            return response

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._logger.error(
                "http.error",
                method=request.method,
                path=str(request.url.path),
                error=str(e),
                latency_ms=round(latency_ms, 2),
                request_id=request_id,
            )
            raise
