"""
TAOS API — FastAPI Application Entry Point.

Production-ready FastAPI app with:
- CORS middleware
- Request logging
- Global error handling
- Route registration
- Lifespan management
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from taos.apps.api.errors import http_exception_handler, validation_exception_handler
from taos.apps.api.middleware.auth import AuthMiddleware
from taos.apps.api.middleware.error_handler import ErrorHandlerMiddleware
from taos.apps.api.middleware.logging import RequestLoggingMiddleware
from taos.apps.api.routes import admin, agent, execute, health, debug, tasks, progress, feedback, workflows, notifications, chats, push, users, billing, documents, memory, memory_portability, memory_onboarding, memory_conflicts, memory_spaces, memory_privacy
from taos.config.env_validation import validate_runtime_environment
from taos.config.settings import get_settings
from taos.core.persistence.runtime_status import build_persistence_status
from taos.infra.logging.logger import TAOSLogger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown hooks."""
    logger = TAOSLogger(name="taos.startup")
    settings = get_settings()

    logger.info(
        "app.starting",
        environment=settings.taos_env,
        host=settings.api_host,
        port=settings.api_port,
        has_openrouter_key=bool(settings.openrouter_api_key),
        has_serper_key=bool(settings.serper_api_key),
    )
    env_report = validate_runtime_environment(settings)
    if env_report["warnings"]:
        logger.warning("startup.env_validation_warning", warnings=env_report["warnings"])
    if not env_report["ok"]:
        logger.error("startup.env_validation_failed", errors=env_report["errors"])
        if settings.is_production:
            raise RuntimeError("Startup environment validation failed in production.")

    persistence = build_persistence_status(settings=settings, check_runtime=True)
    logger.info(
        "persistence.backend",
        requested_backend=str(settings.storage_backend or "memory").lower(),
        mode=persistence.get("persistence_mode"),
        firebase_admin_available=bool(persistence.get("firebase_admin_available")),
        firestore_ready=persistence.get("firestore_ready"),
        fallback_reason=persistence.get("fallback_reason"),
        production_blocking=bool(persistence.get("production_blocking")),
    )
    if persistence.get("production_blocking"):
        raise RuntimeError("Production persistence requirements are not satisfied.")

    await tasks.start_scheduler()

    yield
    await tasks.stop_scheduler()
    logger.info("app.shutdown")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="TAOS AgentOS",
        description=(
            "The Agent Operating System — A production-grade autonomous AI agent "
            "with FSM-driven execution, LLM planning, tool governance, and self-reflection."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
    )

    # ─── Middleware (order matters: last added = first executed) ───
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.is_development else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ─── Routes ───
    app.include_router(agent.router)   # Primary: POST /execute
    app.include_router(admin.router)  # Admin-only verification endpoints
    app.include_router(tasks.router)   # Task automation: /tasks/*
    app.include_router(notifications.router)  # In-app notification feed
    app.include_router(chats.router)  # Chat sessions
    app.include_router(push.router)  # Push token registration
    app.include_router(users.router)  # User profile bootstrap/lookup
    app.include_router(billing.router)  # Membership/payment endpoints
    app.include_router(workflows.router)  # Workflow automation: /workflows/*
    app.include_router(progress.router) # Progress: /progress/*
    app.include_router(feedback.router) # Feedback memory ingestion
    app.include_router(memory.router)  # User-controlled memory center
    app.include_router(memory_portability.router)  # Memory import/export center
    app.include_router(memory_onboarding.router)  # Memory onboarding wizard
    app.include_router(memory_conflicts.router)  # Memory conflict resolver
    app.include_router(memory_spaces.router)  # Team/project shared memory spaces
    app.include_router(memory_privacy.router)  # Memory privacy/export/delete compliance
    app.include_router(documents.router)  # Document upload/process/status/ask
    app.include_router(health.router)  # GET /health
    app.include_router(execute.router, prefix="/v1")  # Legacy: /v1/execute

    if settings.is_development:
        app.include_router(debug.router)

    # standardized error contract
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    return app


# The app instance — imported by uvicorn
app = create_app()
