"""Standard API error helpers and handlers."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID", "unknown")


def build_error(
    *,
    code: str,
    message: str,
    request_id: str,
    route: Optional[str] = None,
    owner: Optional[str] = None,
    retry_after_seconds: Optional[int] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict:
    payload: dict[str, Any] = {
        "error_code": code,
        "code": str(code or "").lower(),
        "error": message,
        "message": message,
        "request_id": request_id,
    }
    safe_route = str(route or "").strip()
    if safe_route:
        payload["route"] = safe_route
    safe_owner = str(owner or "").strip()
    if safe_owner:
        payload["owner"] = safe_owner
    if retry_after_seconds is not None:
        payload["retry_after_seconds"] = int(max(0, int(retry_after_seconds)))
    if extra:
        for key, value in dict(extra).items():
            if key in payload:
                continue
            payload[str(key)] = value
    return payload


def raise_api_error(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    *,
    route: Optional[str] = None,
    owner: Optional[str] = None,
    retry_after_seconds: Optional[int] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    detail = build_error(
        code=code,
        message=message,
        request_id=request_id,
        route=route,
        owner=owner,
        retry_after_seconds=retry_after_seconds,
        extra=extra,
    )
    headers = None
    if retry_after_seconds is not None:
        headers = {"Retry-After": str(int(max(0, int(retry_after_seconds))))}
    raise HTTPException(status_code=status_code, detail=detail, headers=headers)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    rid = _request_id(request)
    detail = exc.detail
    if isinstance(detail, dict) and {"error_code", "message", "request_id"}.issubset(detail.keys()):
        payload = detail
    else:
        payload = build_error(
            code=f"HTTP_{exc.status_code}",
            message=str(detail) if detail else "Request failed",
            request_id=rid,
        )
    headers = dict(exc.headers or {})
    return JSONResponse(status_code=exc.status_code, content=payload, headers=headers)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    rid = _request_id(request)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=build_error(
            code="VALIDATION_ERROR",
            message="Invalid request payload",
            request_id=rid,
        ),
    )
