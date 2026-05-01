"""
TAOS Built-in Tool: HTTP Request Client.

Makes HTTP requests to external APIs/URLs with:
- Method support (GET, POST, PUT, DELETE, PATCH)
- Header and body support
- Timeout enforcement
- Response truncation for large payloads
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from taos.core.tools.registry import ToolDefinition, ToolPolicy
from taos.config.constants import ToolRiskLevel


async def http_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 15,
) -> Dict[str, Any]:
    """
    Make an HTTP request.
    
    Args:
        url: Target URL
        method: HTTP method (GET, POST, PUT, DELETE, PATCH)
        headers: Optional request headers
        body: Optional request body (for POST/PUT/PATCH)
        timeout: Request timeout in seconds
    
    Returns:
        Dict with status_code, headers, body, and success flag
    """
    method = method.upper()
    valid_methods = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"}
    
    if method not in valid_methods:
        return {
            "success": False,
            "error": f"Invalid method: {method}. Valid: {valid_methods}",
            "status_code": None,
        }

    if not url or not url.startswith(("http://", "https://")):
        return {
            "success": False,
            "error": f"Invalid URL: {url}. Must start with http:// or https://",
            "status_code": None,
        }

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers or {},
                json=body if body and method in ("POST", "PUT", "PATCH") else None,
            )

        # ─── Truncate large responses ───
        response_text = response.text[:20000] if response.text else ""

        # ─── Try to parse as JSON ───
        response_json = None
        try:
            response_json = response.json()
        except Exception:
            pass

        return {
            "success": 200 <= response.status_code < 400,
            "status_code": response.status_code,
            "response_body": response_json if response_json else response_text,
            "response_headers": dict(response.headers),
            "content_type": response.headers.get("content-type", ""),
            "content_length": len(response.content),
        }

    except httpx.TimeoutException:
        return {
            "success": False,
            "error": f"Request timed out after {timeout}s",
            "status_code": None,
        }
    except httpx.ConnectError as e:
        return {
            "success": False,
            "error": f"Connection failed: {str(e)}",
            "status_code": None,
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"HTTP request failed: {str(e)}",
            "status_code": None,
        }


def create_http_request_tool() -> ToolDefinition:
    """Factory function to create the HTTP request tool definition."""
    return ToolDefinition(
        name="http_request",
        description="Make HTTP requests to external URLs/APIs. Supports GET, POST, PUT, DELETE, PATCH.",
        input_schema={
            "url": "str",
            "method": "str",
            "headers": "dict",
            "body": "dict",
            "timeout": "int",
        },
        handler=http_request,
        policy=ToolPolicy(
            allowed_roles=["agent"],
            max_calls_per_task=20,
            risk_level=ToolRiskLevel.MEDIUM,
            audit_required=False,
        ),
        rate_limit=30,
        cost_estimate=0.0,
        timeout=20,
        tags=["http", "api", "web"],
    )
