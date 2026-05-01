"""
TAOS built-in tool: Web search via Serper API.

Provides web search capabilities using the Serper.dev Google Search API.
Returns structured results with title, link, snippet, and position.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from taos.config.constants import ToolRiskLevel
from taos.config.settings import get_settings
from taos.core.reliability.provider_health import GLOBAL_PROVIDER_HEALTH, provider_health_snapshot
from taos.core.reliability.provider_policy import get_provider_policy
from taos.core.tools.registry import ToolDefinition, ToolPolicy


def _serper_request_target(base_url: str, search_type: str) -> str:
    normalized = str(base_url or "https://google.serper.dev").strip().rstrip("/")
    desired = str(search_type or "search").strip().lower() or "search"
    lowered = normalized.lower()
    if lowered.endswith(f"/{desired}"):
        return normalized
    if lowered.endswith("/search") and desired == "search":
        return normalized
    if lowered.endswith("/news") and desired == "news":
        return normalized
    if lowered.endswith("/images") and desired == "images":
        return normalized
    return f"{normalized}/{desired}"


def _request_debug_fields(*, url: str, payload: Dict[str, Any], headers: Dict[str, str], response: Any = None, error: Any = None) -> Dict[str, Any]:
    status_code = int(getattr(response, "status_code", 0) or getattr(getattr(error, "response", None), "status_code", 0) or 0)
    preview = str(error).strip() if error is not None else ""
    response_obj = getattr(error, "response", None) or response
    if response_obj is not None:
        try:
            body_preview = str(getattr(response_obj, "text", "") or "").strip()
            if body_preview:
                preview = f"{preview} :: {body_preview}" if preview else body_preview
        except Exception:
            pass
    return {
        "method": "POST",
        "endpoint_host": urlparse(url).netloc or url,
        "endpoint_path": urlparse(url).path or "/",
        "body_keys": sorted(str(key) for key in payload.keys()),
        "has_q": bool(str(payload.get("q") or "").strip()),
        "content_type": str(headers.get("Content-Type") or "").strip(),
        "auth_header_present": bool(str(headers.get("X-API-KEY") or "").strip()),
        "http_status": status_code or None,
        "response_error_preview": preview[:200],
    }


def _extract_result_items(search_type: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if search_type == "news":
        return list(data.get("news", []) or data.get("organic", []) or [])
    if search_type == "images":
        return list(data.get("images", []) or [])
    return list(data.get("organic", []) or [])


def _normalize_result(item: Dict[str, Any], position_fallback: int) -> Optional[Dict[str, Any]]:
    title = str(item.get("title") or item.get("source") or "").strip()
    link = str(item.get("link") or item.get("url") or "").strip()
    snippet = str(
        item.get("snippet")
        or item.get("description")
        or item.get("title")
        or ""
    ).strip()
    if not title or not link:
        return None
    return {
        "title": title,
        "link": link,
        "snippet": snippet,
        "position": int(item.get("position", position_fallback) or position_fallback),
    }


async def web_search(
    query: str,
    num_results: int = 5,
    search_type: str = "search",
    recency_days: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Search the web using Serper API.

    Args:
        query: Search query string
        num_results: Number of results to return (1-10)
        search_type: Type of search ("search", "news", "images")

    Returns:
        Dict with normalized search results.
    """
    settings = get_settings()
    policy = get_provider_policy("serper")

    if not settings.serper_api_key:
        GLOBAL_PROVIDER_HEALTH.mark_fallback("serper")
        debug = _request_debug_fields(
            url=_serper_request_target(str(settings.serper_base_url or "https://google.serper.dev"), search_type),
            payload={"q": query},
            headers={"Content-Type": "application/json", "X-API-KEY": "***"},
            error="missing_api_key",
        )
        return {
            "error": "Serper API key not configured",
            "error_type": "missing_api_key",
            "error_safe": "Serper API key not configured",
            "results": [],
            "query": query,
            "search_type": search_type,
            "recency_days": recency_days,
            "total_results": 0,
            "provider_name": "serper",
            "search_api_key_present": False,
            "search_endpoint_configured": bool(str(settings.serper_base_url or "").strip()),
            "request_debug": debug,
            "provider_health": provider_health_snapshot(),
        }
    if not GLOBAL_PROVIDER_HEALTH.allow_request("serper"):
        GLOBAL_PROVIDER_HEALTH.mark_fallback("serper")
        return {
            "error": "web_search_provider_unavailable: circuit_open",
            "error_type": "circuit_open",
            "error_safe": "web_search_provider_unavailable: circuit_open",
            "results": [],
            "query": query,
            "search_type": search_type,
            "recency_days": recency_days,
            "total_results": 0,
            "provider_name": "serper",
            "search_api_key_present": True,
            "search_endpoint_configured": bool(str(settings.serper_base_url or "").strip()),
            "provider_health": provider_health_snapshot(),
        }

    headers = {
        "X-API-KEY": settings.serper_api_key,
        "Content-Type": "application/json",
    }

    payload: Dict[str, Any] = {
        "q": query,
    }
    bounded_num = max(1, min(int(num_results or 5), 10))
    if bounded_num != 5:
        payload["num"] = bounded_num
    if recency_days is not None and recency_days > 0:
        if recency_days <= 1:
            payload["tbs"] = "qdr:d"
        elif recency_days <= 7:
            payload["tbs"] = "qdr:w"
        elif recency_days <= 31:
            payload["tbs"] = "qdr:m"
        else:
            payload["tbs"] = "qdr:y"

    base_url = str(settings.serper_base_url or "https://google.serper.dev").strip().rstrip("/")
    url = _serper_request_target(base_url, search_type)
    data: Dict[str, Any]
    last_exc: Exception | None = None
    last_response: Any = None
    for attempt in range(int(policy.max_retries) + 1):
        try:
            async with httpx.AsyncClient(timeout=policy.timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
                last_response = response
                response.raise_for_status()
            data = response.json()
            GLOBAL_PROVIDER_HEALTH.record_success("serper")
            break
        except Exception as exc:
            last_exc = exc
            if attempt < int(policy.max_retries):
                continue
            GLOBAL_PROVIDER_HEALTH.record_failure("serper", exc)
            GLOBAL_PROVIDER_HEALTH.mark_fallback("serper")
            status_code = int(getattr(getattr(exc, "response", None), "status_code", 0) or 0)
            error_type = type(exc).__name__
            error_safe = str(exc).strip() or error_type
            if status_code == 400:
                error_type = "request_contract_error"
                error_safe = "Serper request contract rejected the search payload."
            return {
                "error": f"web_search_failed: {error_type}",
                "error_type": error_type,
                "error_safe": error_safe[:200],
                "results": [],
                "query": query,
                "search_type": search_type,
                "recency_days": recency_days,
                "total_results": 0,
                "provider_name": "serper",
                "search_api_key_present": True,
                "search_endpoint_configured": bool(base_url),
                "search_http_status": status_code or None,
                "search_endpoint": urlparse(base_url).netloc or base_url,
                "request_debug": _request_debug_fields(url=url, payload=payload, headers=headers, response=last_response, error=exc),
                "provider_health": provider_health_snapshot(),
            }
    else:
        GLOBAL_PROVIDER_HEALTH.record_failure("serper", last_exc or "unknown")
        GLOBAL_PROVIDER_HEALTH.mark_fallback("serper")
        return {
            "error": "web_search_failed: unknown",
            "error_type": "unknown",
            "error_safe": "web_search_failed: unknown",
            "results": [],
            "query": query,
            "search_type": search_type,
            "recency_days": recency_days,
            "total_results": 0,
            "provider_name": "serper",
            "search_api_key_present": True,
            "search_endpoint_configured": bool(base_url),
            "search_http_status": None,
            "search_endpoint": urlparse(base_url).netloc or base_url,
            "request_debug": _request_debug_fields(url=url, payload=payload, headers=headers, response=last_response, error=last_exc),
            "provider_health": provider_health_snapshot(),
        }

    raw_items = _extract_result_items(search_type, data)
    results: List[Dict[str, Any]] = []
    limit = int(payload.get("num") or bounded_num)
    for idx, item in enumerate(raw_items[:limit], start=1):
        if not isinstance(item, dict):
            continue
        normalized = _normalize_result(item, position_fallback=idx)
        if normalized is not None:
            results.append(normalized)

    knowledge_graph = None
    if "knowledgeGraph" in data and isinstance(data["knowledgeGraph"], dict):
        kg = data["knowledgeGraph"]
        knowledge_graph = {
            "title": kg.get("title", ""),
            "type": kg.get("type", ""),
            "description": kg.get("description", ""),
        }

    return {
        "query": query,
        "search_type": search_type,
        "recency_days": recency_days,
        "results": results,
        "knowledge_graph": knowledge_graph,
        "total_results": len(results),
        "provider_name": "serper",
        "search_api_key_present": True,
        "search_endpoint_configured": bool(base_url),
        "search_http_status": 200,
        "search_endpoint": urlparse(base_url).netloc or base_url,
        "request_debug": _request_debug_fields(url=url, payload=payload, headers=headers, response=last_response),
        "provider_health": provider_health_snapshot(),
    }


def create_web_search_tool() -> ToolDefinition:
    """Factory function to create the web search tool definition."""
    return ToolDefinition(
        name="web_search",
        description=(
            "Search the web via Serper API. For research queries, use strict, descriptive "
            "keywords (for example 'tamil nadu startup funding statistics 2023 2024 data' "
            "instead of 'tamil nadu startup'). Returns titles, snippets, and links."
        ),
        input_schema={
            "query": "str",
            "num_results": "int",
            "search_type": "str",
            "recency_days": "int",
        },
        handler=web_search,
        policy=ToolPolicy(
            allowed_roles=["agent"],
            max_calls_per_task=10,
            risk_level=ToolRiskLevel.LOW,
            audit_required=False,
        ),
        rate_limit=30,
        cost_estimate=0.001,
        timeout=15,
        tags=["search", "web", "information"],
    )
