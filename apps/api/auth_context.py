"""Helpers for request auth scope."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Request

from taos.apps.api.errors import raise_api_error
from taos.infra.auth import build_user_info, is_admin_claims, is_superadmin_claims


def require_uid(request: Request) -> str:
    uid = str(getattr(request.state, "auth_uid", "") or "")
    if not uid:
        rid = request.headers.get("X-Request-ID", "unknown")
        raise_api_error(401, "AUTH_INVALID_TOKEN", "Invalid token", rid)
    return uid


def resolve_user_id(request: Request, provided_user_id: str | None) -> str:
    uid = require_uid(request)
    if bool(getattr(request.state, "auth_dev_bypass", False)) and provided_user_id:
        request.state.auth_uid = str(provided_user_id)
        claims = dict(getattr(request.state, "auth_claims", {}) or {})
        claims["uid"] = str(provided_user_id)
        request.state.auth_claims = claims
        request.state.auth_user = build_user_info(claims)
        return str(provided_user_id)
    if provided_user_id and provided_user_id != uid:
        rid = request.headers.get("X-Request-ID", "unknown")
        raise_api_error(403, "AUTH_FORBIDDEN", "Forbidden for this user scope", rid)
    return uid


def current_user_info(request: Request) -> Dict[str, Any]:
    cached = getattr(request.state, "auth_user", None)
    if isinstance(cached, dict) and cached.get("uid"):
        return cached
    claims = getattr(request.state, "auth_claims", None)
    if isinstance(claims, dict) and claims.get("uid"):
        return build_user_info(claims)
    rid = request.headers.get("X-Request-ID", "unknown")
    raise_api_error(401, "AUTH_INVALID_TOKEN", "Invalid token", rid)
    return {}


def require_admin(request: Request) -> Dict[str, Any]:
    user = current_user_info(request)
    if not is_admin_claims(user.get("claims", {})):
        rid = request.headers.get("X-Request-ID", "unknown")
        raise_api_error(403, "AUTH_FORBIDDEN", "Admin required", rid)
    return user


def require_superadmin(request: Request) -> Dict[str, Any]:
    user = current_user_info(request)
    if not is_superadmin_claims(user.get("claims", {})):
        rid = request.headers.get("X-Request-ID", "unknown")
        raise_api_error(403, "AUTH_FORBIDDEN", "Superadmin required", rid)
    return user
