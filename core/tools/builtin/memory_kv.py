"""
TAOS Built-in Tools: Memory KV.

Simple short-term key-value memory abstraction for user/session scoped context.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from taos.config.constants import ToolRiskLevel
from taos.core.tools.registry import ToolDefinition, ToolPolicy


_MEMORY: Dict[str, Dict[str, Any]] = {}


def _scope_key(user_id: str, session_id: str | None) -> str:
    uid = str(user_id or "").strip() or "anonymous"
    sid = str(session_id or "").strip() or "default"
    return f"{uid}:{sid}"


async def memory_set(
    key: str,
    value: Any,
    user_id: str,
    session_id: str | None = None,
    ttl_seconds: int | None = None,
) -> Dict[str, Any]:
    k = str(key or "").strip()
    if not k:
        return {"success": False, "error": "key is required"}
    scope = _scope_key(user_id=user_id, session_id=session_id)
    expires_at = None
    if ttl_seconds is not None and int(ttl_seconds) > 0:
        expires_at = time.time() + int(ttl_seconds)
    bucket = _MEMORY.setdefault(scope, {})
    bucket[k] = {"value": value, "updated_at": time.time(), "expires_at": expires_at}
    return {"success": True, "scope": scope, "key": k}


async def memory_get(
    key: str,
    user_id: str,
    session_id: str | None = None,
    default: Any = None,
) -> Dict[str, Any]:
    k = str(key or "").strip()
    if not k:
        return {"success": False, "error": "key is required"}
    scope = _scope_key(user_id=user_id, session_id=session_id)
    bucket = _MEMORY.get(scope, {})
    item = bucket.get(k)
    if not item:
        return {"success": True, "scope": scope, "key": k, "found": False, "value": default}
    expires_at = item.get("expires_at")
    if expires_at and time.time() > float(expires_at):
        bucket.pop(k, None)
        return {"success": True, "scope": scope, "key": k, "found": False, "value": default, "expired": True}
    return {
        "success": True,
        "scope": scope,
        "key": k,
        "found": True,
        "value": item.get("value"),
        "updated_at": item.get("updated_at"),
    }


def create_memory_set_tool() -> ToolDefinition:
    return ToolDefinition(
        name="memory_set",
        description="Store a short-term memory value under a scoped key (user/session).",
        input_schema={
            "key": "str",
            "value": "any",
            "user_id": "str",
            "session_id": "str",
            "ttl_seconds": "int",
        },
        handler=memory_set,
        policy=ToolPolicy(
            allowed_roles=["agent"],
            max_calls_per_task=50,
            risk_level=ToolRiskLevel.LOW,
            audit_required=False,
        ),
        rate_limit=120,
        cost_estimate=0.0,
        timeout=10,
        tags=["memory", "kv", "context"],
    )


def create_memory_get_tool() -> ToolDefinition:
    return ToolDefinition(
        name="memory_get",
        description="Read a short-term memory value by key from user/session scope.",
        input_schema={
            "key": "str",
            "user_id": "str",
            "session_id": "str",
            "default": "any",
        },
        handler=memory_get,
        policy=ToolPolicy(
            allowed_roles=["agent"],
            max_calls_per_task=80,
            risk_level=ToolRiskLevel.LOW,
            audit_required=False,
        ),
        rate_limit=150,
        cost_estimate=0.0,
        timeout=10,
        tags=["memory", "kv", "context"],
    )

