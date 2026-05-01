from __future__ import annotations

import importlib.util
from typing import Any, Dict, Optional

from taos.config.settings import Settings, get_settings


def _present(value: Any) -> bool:
    return bool(str(value or "").strip())


def _redact_error(error: str) -> str:
    text = str(error or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if "firebase-admin" in lower or "firebase_admin" in lower:
        return "firebase_admin_not_installed"
    if "database (default) does not exist" in lower or "firestore" in lower and "does not exist" in lower:
        return "firestore_database_missing"
    if "credential" in lower or "private key" in lower or "client_email" in lower:
        return "firebase_credentials_invalid_or_missing"
    if len(text) > 160:
        return text[:157] + "..."
    return text


def firebase_admin_available() -> bool:
    return importlib.util.find_spec("firebase_admin") is not None


def firebase_configured(settings: Settings) -> bool:
    return all(
        _present(value)
        for value in (
            settings.firebase_project_id,
            settings.firebase_client_email,
            settings.firebase_private_key,
        )
    )


def build_persistence_status(
    *,
    settings: Optional[Settings] = None,
    firestore_ready: Optional[bool] = None,
    last_error: str = "",
    check_runtime: bool = True,
) -> Dict[str, Any]:
    settings = settings or get_settings()
    storage_backend = str(settings.storage_backend or "memory").strip().lower()
    requested_firebase = storage_backend in {"firebase", "firestore"}
    admin_available = firebase_admin_available()
    configured = firebase_configured(settings) if requested_firebase else False
    strict_mode = bool(settings.is_production_like)
    fallback_allowed = bool(settings.is_development or not strict_mode or settings.allow_memory_fallback_in_production)

    resolved_firestore_ready = firestore_ready
    fallback_reason = ""
    last_persistence_error = _redact_error(last_error)

    if requested_firebase and check_runtime and resolved_firestore_ready is None and admin_available and configured:
        try:
            from taos.infra.persistence.firebase_store import FirestoreStore

            store = FirestoreStore()
            resolved_firestore_ready = bool(store.is_available)
            if not last_persistence_error:
                last_persistence_error = _redact_error(getattr(store, "last_error", ""))
        except Exception as exc:  # pragma: no cover - defensive boundary
            resolved_firestore_ready = False
            last_persistence_error = _redact_error(str(exc))
    elif resolved_firestore_ready is None:
        resolved_firestore_ready = False if requested_firebase else None

    if requested_firebase:
        if not admin_available:
            fallback_reason = "firebase_admin_not_installed"
        elif not configured:
            fallback_reason = "firebase_not_configured"
        elif resolved_firestore_ready is False:
            fallback_reason = last_persistence_error or "firestore_not_ready"
    else:
        fallback_reason = "storage_backend_memory"

    if requested_firebase and resolved_firestore_ready:
        persistence_mode = "firestore"
    elif storage_backend == "memory":
        persistence_mode = "memory_fallback"
    elif requested_firebase and fallback_allowed:
        persistence_mode = "memory_fallback"
    else:
        persistence_mode = "disabled"

    production_blocking = bool(
        strict_mode
        and (
            persistence_mode != "firestore"
            and not bool(settings.allow_memory_fallback_in_production)
        )
    )

    return {
        "requested_backend": storage_backend,
        "persistence_mode": persistence_mode,
        "firebase_admin_available": admin_available,
        "firebase_configured": configured if requested_firebase else None,
        "firestore_ready": resolved_firestore_ready,
        "fallback_reason": fallback_reason or None,
        "production_blocking": production_blocking,
        "last_persistence_error": last_persistence_error or None,
    }
