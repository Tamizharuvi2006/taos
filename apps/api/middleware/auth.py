"""Strict Firebase auth middleware for protected routes."""

from __future__ import annotations

import hashlib
import time
from typing import Callable

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from taos.apps.api.errors import build_error
from taos.config.settings import get_settings
from taos.core.security import dev_bypass_allowed
from taos.infra.auth import (
    FirebaseAuthError,
    build_user_info,
    get_claim_role,
    get_firebase_verifier,
    is_admin_claims,
    is_superadmin_claims,
)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self._settings = get_settings()
        self._verifier = None
        self._token_cache: dict[str, tuple[float, dict]] = {}
        self._token_cache_ttl_seconds = float(
            max(30, int(getattr(self._settings, "auth_token_cache_ttl_seconds", 180) or 180))
        )
        self._token_cache_max_entries = int(
            max(64, int(getattr(self._settings, "auth_token_cache_max_entries", 2048) or 2048))
        )
        self._protected_prefixes = (
            "/execute",
            "/v1",
            "/debug",
            "/admin",
            "/api",
            "/tasks",
            "/notifications",
            "/chats",
            "/push",
            "/users",
            "/payment",
            "/workflows",
            "/feedback",
            "/memory",
            "/progress",
        )
        self._public_prefixes = (
            "/health",
            "/warmup",
            "/docs",
            "/redoc",
            "/openapi.json",
        )

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path
        if request.method.upper() == "OPTIONS":
            return await call_next(request)
        if any(path.startswith(p) for p in self._public_prefixes):
            return await call_next(request)

        if any(path.startswith(p) for p in self._protected_prefixes):
            rid = request.headers.get("X-Request-ID", "unknown")
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                if self._apply_dev_bypass(request):
                    return await call_next(request)
                return JSONResponse(
                    status_code=401,
                    content=build_error(
                        code="AUTH_MISSING_TOKEN",
                        message="Missing Bearer token",
                        request_id=rid,
                    ),
                )
            token = auth_header.replace("Bearer ", "", 1).strip()
            if not token:
                return JSONResponse(
                    status_code=401,
                    content=build_error(
                        code="AUTH_INVALID_TOKEN",
                        message="Invalid token",
                        request_id=rid,
                    ),
                )
            try:
                decoded = self._get_cached_or_verify_token(token)
                user_info = build_user_info(decoded)
                request.state.auth_uid = str(decoded.get("uid"))
                request.state.auth_claims = decoded
                request.state.auth_user = user_info
                request.state.auth_role = get_claim_role(decoded)
                request.state.auth_is_admin = is_admin_claims(decoded)
                request.state.auth_is_superadmin = is_superadmin_claims(decoded)
            except FirebaseAuthError:
                if self._apply_dev_bypass(request):
                    return await call_next(request)
                return JSONResponse(
                    status_code=401,
                    content=build_error(
                        code="AUTH_INVALID_TOKEN",
                        message="Invalid token",
                        request_id=rid,
                    ),
                )

        return await call_next(request)

    def _apply_dev_bypass(self, request: Request) -> bool:
        if not dev_bypass_allowed(
            taos_env=self._settings.taos_env,
            auth_allow_dev_bypass=self._settings.auth_allow_dev_bypass,
        ):
            return False
        debug_uid = (
            request.headers.get("X-User-ID")
            or request.headers.get("X-Debug-UID")
            or "dev_local_user"
        ).strip()
        # Development-only local bypass for protected-route QA.
        claims = {"uid": debug_uid, "role": "user"}
        request.state.auth_uid = debug_uid
        request.state.auth_claims = claims
        request.state.auth_user = build_user_info(claims)
        request.state.auth_role = "user"
        request.state.auth_is_admin = False
        request.state.auth_is_superadmin = False
        request.state.auth_dev_bypass = True
        return True

    def _get_cached_or_verify_token(self, token: str) -> dict:
        now = time.time()
        cache_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        cached = self._token_cache.get(cache_key)
        if cached:
            expires_at, claims = cached
            if float(expires_at or 0.0) > now:
                return claims
            self._token_cache.pop(cache_key, None)

        if self._verifier is None:
            self._verifier = get_firebase_verifier()
        decoded = self._verifier.verify(token)

        token_exp = float(decoded.get("exp") or 0.0)
        max_cache_exp = now + self._token_cache_ttl_seconds
        cache_exp = max_cache_exp
        if token_exp > 0:
            cache_exp = min(max_cache_exp, max(now + 1.0, token_exp - 5.0))
        if cache_exp > now:
            self._token_cache[cache_key] = (cache_exp, decoded)
            self._prune_token_cache(now=now)
        return decoded

    def _prune_token_cache(self, now: float | None = None) -> None:
        current = float(now if now is not None else time.time())
        if len(self._token_cache) <= self._token_cache_max_entries:
            expired = [k for k, (exp, _) in self._token_cache.items() if float(exp or 0.0) <= current]
            for key in expired:
                self._token_cache.pop(key, None)
            return

        items = sorted(self._token_cache.items(), key=lambda item: float(item[1][0] or 0.0))
        overflow = max(0, len(items) - self._token_cache_max_entries)
        for idx, (key, (exp, _claims)) in enumerate(items):
            if idx < overflow or float(exp or 0.0) <= current:
                self._token_cache.pop(key, None)
