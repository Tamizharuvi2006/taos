from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from taos.apps.api.auth_context import resolve_user_id
from taos.apps.api.middleware.auth import AuthMiddleware
from taos.infra.auth import FirebaseAuthError


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/users/me",
            "query_string": b"",
            "headers": headers,
        }
    )


@pytest.mark.asyncio
async def test_dev_bypass_allows_invalid_bearer_token_in_development() -> None:
    middleware = AuthMiddleware(FastAPI())
    middleware._settings = SimpleNamespace(taos_env="development", auth_allow_dev_bypass=True)

    def _boom(_token: str) -> dict:
        raise FirebaseAuthError("invalid")

    middleware._get_cached_or_verify_token = _boom
    request = _request([(b"authorization", b"Bearer stale-token"), (b"x-user-id", b"local_smoke_user")])

    async def call_next(req: Request):
        return JSONResponse({"uid": req.state.auth_uid, "role": req.state.auth_role}, status_code=200)

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 200
    assert b"local_smoke_user" in response.body


@pytest.mark.asyncio
async def test_dev_bypass_can_adopt_provided_user_scope() -> None:
    middleware = AuthMiddleware(FastAPI())
    middleware._settings = SimpleNamespace(taos_env="development", auth_allow_dev_bypass=True)

    def _boom(_token: str) -> dict:
        raise FirebaseAuthError("invalid")

    middleware._get_cached_or_verify_token = _boom
    request = _request([(b"authorization", b"Bearer stale-token")])

    async def call_next(req: Request):
        resolved = resolve_user_id(req, "firebase_frontend_uid")
        return JSONResponse({"uid": resolved}, status_code=200)

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 200
    assert b"firebase_frontend_uid" in response.body


@pytest.mark.asyncio
async def test_invalid_token_still_rejected_without_dev_bypass() -> None:
    middleware = AuthMiddleware(FastAPI())
    middleware._settings = SimpleNamespace(taos_env="production", auth_allow_dev_bypass=False)

    def _boom(_token: str) -> dict:
        raise FirebaseAuthError("invalid")

    middleware._get_cached_or_verify_token = _boom
    request = _request([(b"authorization", b"Bearer stale-token")])

    async def call_next(_req: Request):
        raise AssertionError("call_next should not run when token is invalid outside dev bypass")

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 401
    assert b"Invalid token" in response.body
