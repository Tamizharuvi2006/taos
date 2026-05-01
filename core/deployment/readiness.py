"""Deployment readiness checks for health endpoints and smoke tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from taos.config.settings import Settings, get_settings
from taos.core.deployment.env_validator import validate_production_environment
from taos.core.monitoring.metrics_collector import DEFAULT_PROVIDERS
from taos.core.persistence.runtime_status import build_persistence_status
from taos.core.reliability.provider_health import provider_health_snapshot


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _frontend_root() -> Path:
    return _repo_root().parent / "frontend"


def _next_build_exists(frontend_root: Path) -> bool:
    return (frontend_root / ".next" / "BUILD_ID").exists() or (frontend_root / ".next" / "server").exists()


def _provider_rows(settings: Settings) -> dict[str, dict[str, Any]]:
    snapshot = provider_health_snapshot()
    rows: dict[str, dict[str, Any]] = {}
    for provider in DEFAULT_PROVIDERS:
        row = dict(snapshot.get(provider) or {})
        row.setdefault("state", "unknown")
        row.setdefault("configured", True)
        rows[provider] = row
    rows["openrouter"]["configured"] = bool(str(settings.openrouter_api_key or "").strip())
    rows["serper"]["configured"] = bool(str(settings.serper_api_key or "").strip())
    return rows


def build_readiness_report(
    *,
    settings: Settings | None = None,
    repo_root: str | Path | None = None,
    frontend_root: str | Path | None = None,
    check_firebase_runtime: bool = True,
) -> dict[str, Any]:
    """Build a secret-free deployment readiness report."""

    settings = settings or get_settings()
    root = Path(repo_root) if repo_root is not None else _repo_root()
    frontend = Path(frontend_root) if frontend_root is not None else _frontend_root()
    env_report = validate_production_environment(settings)
    storage_backend = str(settings.storage_backend or "memory").strip().lower()
    persistence = build_persistence_status(settings=settings, check_runtime=check_firebase_runtime)

    dashboard_json = root / "docs" / "ops_dashboard_latest.json"
    dashboard_md = root / "docs" / "ops_dashboard_latest.md"
    checks: dict[str, Any] = {
        "environment": env_report["checks"].get("environment"),
        "env_ok": bool(env_report.get("ok")),
        "health_endpoint_available": True,
        "ops_dashboard_json_exists": dashboard_json.exists(),
        "ops_dashboard_md_exists": dashboard_md.exists(),
        "provider_health_available": True,
        "frontend_root_exists": frontend.exists(),
        "frontend_build_artifact_exists": _next_build_exists(frontend),
        "firebase_runtime_ready": None,
        "persistence_mode": persistence.get("persistence_mode"),
        "firebase_admin_available": persistence.get("firebase_admin_available"),
        "firestore_ready": persistence.get("firestore_ready"),
        "persistence_fallback_reason": persistence.get("fallback_reason"),
        "persistence_production_blocking": persistence.get("production_blocking"),
        "last_persistence_error": persistence.get("last_persistence_error"),
    }

    errors = list(env_report.get("errors") or [])
    warnings = list(env_report.get("warnings") or [])

    if not checks["ops_dashboard_json_exists"] or not checks["ops_dashboard_md_exists"]:
        errors.append("Ops dashboard artifacts are missing; run scripts/build_ops_dashboard.py.")
    if not checks["frontend_root_exists"]:
        warnings.append(f"Frontend root not found: {frontend}")
    elif not checks["frontend_build_artifact_exists"]:
        warnings.append("Frontend build artifact was not found; run npm run build in D:\\agent\\frontend.")

    checks["firebase_runtime_ready"] = persistence.get("firestore_ready")
    if persistence.get("production_blocking"):
        errors.append(
            "Persistence is not production-ready: "
            + str(persistence.get("fallback_reason") or "firestore_unavailable")
        )
    elif storage_backend in {"firebase", "firestore"} and persistence.get("persistence_mode") == "memory_fallback":
        warnings.append("Firebase persistence is unavailable; development is using explicit memory fallback.")

    checks["provider_health"] = _provider_rows(settings)

    ready = not errors
    return {
        "status": "ready" if ready else "blocked",
        "ready": ready,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }
