from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict
from urllib.parse import quote

import httpx

from taos.core.reliability.provider_health import GLOBAL_PROVIDER_HEALTH, provider_health_snapshot
from taos.core.reliability.provider_policy import get_provider_policy


_CACHE_TTL_SECONDS = 15 * 60
_CACHE: Dict[str, Dict[str, Any]] = {}


def _cache_get(name: str) -> Dict[str, Any] | None:
    cached = _CACHE.get(name)
    if not cached:
        return None
    age = time.time() - float(cached.get("_cached_at", 0.0) or 0.0)
    if age > _CACHE_TTL_SECONDS:
        cached = dict(cached)
        cached["cache_status"] = "stale"
        return cached
    cached = dict(cached)
    cached["cache_status"] = "hit"
    return cached


def _cache_set(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    cached = dict(payload or {})
    cached["_cached_at"] = time.time()
    cached["cache_status"] = "miss"
    _CACHE[name] = cached
    return dict(cached)


async def lookup_npm_latest(package_name: str, *, timeout: float = 2.75) -> Dict[str, Any]:
    """Return npm's source-of-record latest tag for a package."""
    name = str(package_name or "").strip().lower()
    if not name:
        return {"success": False, "error": "missing_package_name"}
    cached = _cache_get(name)
    if cached and cached.get("success"):
        GLOBAL_PROVIDER_HEALTH.mark_fallback("npm_registry", cache_used=True)
        return cached

    policy = get_provider_policy("npm_registry")
    if not GLOBAL_PROVIDER_HEALTH.allow_request("npm_registry"):
        if cached:
            fallback = dict(cached)
            fallback["cache_status"] = "circuit_open_cache_fallback"
            fallback["registry_error"] = "npm_registry_circuit_open"
            fallback["provider_health"] = provider_health_snapshot()
            GLOBAL_PROVIDER_HEALTH.mark_fallback("npm_registry", cache_used=True)
            return fallback
        GLOBAL_PROVIDER_HEALTH.mark_fallback("npm_registry", cache_used=False)
        return {
            "success": False,
            "error": "npm_registry_circuit_open",
            "package_name": name,
            "provider_health": provider_health_snapshot(),
        }

    encoded = quote(name, safe="@")
    url = f"https://registry.npmjs.org/{encoded}"
    try:
        request_timeout = httpx.Timeout(
            timeout=float(timeout or policy.timeout_seconds),
            connect=min(1.25, float(timeout or policy.timeout_seconds)),
            read=float(timeout or policy.timeout_seconds),
            write=1.0,
            pool=0.75,
        )
        async with httpx.AsyncClient(timeout=request_timeout, follow_redirects=True) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        GLOBAL_PROVIDER_HEALTH.record_failure("npm_registry", exc)
        if cached:
            fallback = dict(cached)
            fallback["cache_status"] = "stale_fallback"
            fallback["registry_error"] = str(exc)
            fallback["provider_health"] = provider_health_snapshot()
            GLOBAL_PROVIDER_HEALTH.mark_fallback("npm_registry", cache_used=True)
            return fallback
        GLOBAL_PROVIDER_HEALTH.mark_fallback("npm_registry", cache_used=False)
        return {"success": False, "error": str(exc), "package_name": name, "source_url": url, "provider_health": provider_health_snapshot()}
    GLOBAL_PROVIDER_HEALTH.record_success("npm_registry")

    dist_tags = data.get("dist-tags") if isinstance(data, dict) else {}
    version = str((dist_tags or {}).get("latest") or "").strip()
    versions = data.get("versions") if isinstance(data, dict) else {}
    version_data = versions.get(version) if isinstance(versions, dict) and version else {}
    time_data = data.get("time") if isinstance(data, dict) else {}
    published_at = str((time_data or {}).get(version) or "").strip()
    if published_at:
        try:
            published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass

    if not version:
        return {"success": False, "error": "missing_latest_tag", "package_name": name, "source_url": url}

    payload = _cache_set(name, {
        "success": True,
        "name": str(data.get("name") or name),
        "version": version,
        "published_at": published_at,
        "description": str((version_data or {}).get("description") or data.get("description") or ""),
        "source_url": f"https://www.npmjs.com/package/{name}",
        "registry_url": url,
    })
    payload["provider_health"] = provider_health_snapshot()
    return payload
