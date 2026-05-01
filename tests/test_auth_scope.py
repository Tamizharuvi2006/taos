from __future__ import annotations

import pytest
from fastapi import Request

from taos.apps.api.auth_context import resolve_user_id


def _request_with_uid(uid: str):
    scope = {
        "type": "http",
        "headers": [(b"x-request-id", b"rid1")],
        "method": "GET",
        "path": "/execute",
        "query_string": b"",
    }
    req = Request(scope)
    req.state.auth_uid = uid
    return req


def test_resolve_user_id_matches_token_uid():
    req = _request_with_uid("u1")
    assert resolve_user_id(req, "u1") == "u1"


def test_resolve_user_id_forbidden_on_mismatch():
    req = _request_with_uid("u1")
    with pytest.raises(Exception):
        resolve_user_id(req, "u2")
