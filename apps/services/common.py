"""Shared helpers for TAOS microservice apps."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from taos.apps.api.middleware.error_handler import ErrorHandlerMiddleware
from taos.apps.api.middleware.logging import RequestLoggingMiddleware
from taos.infra.logging.logger import TAOSLogger


@asynccontextmanager
async def service_lifespan(app: FastAPI):
    logger = TAOSLogger(name="taos.service")
    logger.info("service.start", name=app.title)
    yield
    logger.info("service.stop", name=app.title)


def create_service_app(title: str) -> FastAPI:
    app = FastAPI(title=title, version="0.1.0", lifespan=service_lifespan)
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    return app
