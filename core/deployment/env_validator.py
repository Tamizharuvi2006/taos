"""Production deployment environment validation for TAOS."""

from __future__ import annotations

import os
from typing import Any, Mapping

from taos.config.settings import Settings


def _present(value: Any) -> bool:
    return bool(str(value or "").strip())


def _env_value(environ: Mapping[str, str], name: str) -> str:
    return str(environ.get(name) or environ.get(name.lower()) or "").strip()


def _missing(settings: Settings, fields: Mapping[str, Any]) -> list[str]:
    return [name for name, value in fields.items() if not _present(value)]


def validate_production_environment(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a production-focused env report without leaking secret values."""

    env = environ or os.environ
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}

    taos_env = str(settings.taos_env or "").strip().lower()
    is_production = taos_env == "production"
    is_staging = taos_env == "staging"
    is_production_like = is_production or is_staging
    storage_backend = str(settings.storage_backend or "memory").strip().lower()

    checks["environment"] = taos_env or "unknown"
    checks["production_mode"] = is_production
    checks["staging_mode"] = is_staging
    checks["production_like_mode"] = is_production_like
    checks["openrouter_configured"] = _present(settings.openrouter_api_key)
    checks["serper_configured"] = _present(settings.serper_api_key)
    checks["storage_backend"] = storage_backend
    checks["allow_memory_fallback_in_production"] = bool(settings.allow_memory_fallback_in_production)
    checks["debug_disabled"] = not bool(settings.debug)
    checks["dev_bypass_disabled"] = not bool(settings.auth_allow_dev_bypass)
    checks["request_timeout_configured"] = int(settings.max_request_time_seconds or 0) > 0
    checks["step_timeout_configured"] = int(settings.max_step_time or 0) > 0
    checks["max_steps_configured"] = int(settings.max_steps or 0) > 0
    checks["upload_size_configured"] = int(settings.max_upload_size_mb or 0) > 0
    checks["cors_wildcard_absent"] = "*" not in _env_value(env, "CORS_ALLOW_ORIGINS")

    if not checks["openrouter_configured"]:
        errors.append("OPENROUTER_API_KEY is missing.")
    if is_production_like and not checks["serper_configured"]:
        warnings.append("SERPER_API_KEY is missing; live web search quality may be reduced.")
    if is_production_like and bool(settings.auth_allow_dev_bypass):
        errors.append("AUTH_ALLOW_DEV_BYPASS must be false in production/staging.")
    if is_production_like and bool(settings.debug):
        errors.append("DEBUG must be false in production/staging.")
    if is_production_like and not checks["cors_wildcard_absent"]:
        errors.append("CORS_ALLOW_ORIGINS must not contain '*' in production/staging.")
    if int(settings.max_request_time_seconds or 0) <= 0:
        errors.append("MAX_REQUEST_TIME_SECONDS must be > 0.")
    if int(settings.max_step_time or 0) <= 0:
        errors.append("MAX_STEP_TIME must be > 0.")
    if int(settings.max_steps or 0) <= 0:
        errors.append("MAX_STEPS must be > 0.")
    if int(settings.max_upload_size_mb or 0) <= 0:
        errors.append("MAX_UPLOAD_SIZE_MB must be > 0.")

    if storage_backend in {"firebase", "firestore"}:
        firebase_fields = {
            "FIREBASE_PROJECT_ID": settings.firebase_project_id,
            "FIREBASE_CLIENT_EMAIL": settings.firebase_client_email,
            "FIREBASE_PRIVATE_KEY": settings.firebase_private_key,
        }
        missing_firebase = _missing(settings, firebase_fields)
        checks["firebase_configured"] = not missing_firebase
        if missing_firebase:
            message = (
                "Firebase storage is enabled but required FIREBASE_* variables are missing: "
                + ", ".join(missing_firebase)
            )
            if is_production_like:
                errors.append(message)
            else:
                warnings.append(message)
    else:
        checks["firebase_configured"] = None
        if is_production_like and storage_backend == "memory" and not bool(settings.allow_memory_fallback_in_production):
            errors.append("STORAGE_BACKEND=memory is blocked in production/staging unless ALLOW_MEMORY_FALLBACK_IN_PRODUCTION=true.")
        elif is_production_like and storage_backend == "memory":
            warnings.append("STORAGE_BACKEND=memory is running in production/staging by explicit override.")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }
