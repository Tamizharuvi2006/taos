from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_PARENT = _REPO_ROOT.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))

from taos.config.settings import get_settings  # noqa: E402
from taos.core.reliability.provider_health import provider_health_snapshot  # noqa: E402
from taos.core.tools.builtin.extract_adapter import extract_with_adapter  # noqa: E402
from taos.core.tools.builtin.web_search import web_search  # noqa: E402
from taos.orchestration.engine import OrchestrationEngine  # noqa: E402


DEFAULT_JSON = _REPO_ROOT / "PROVIDER_CONNECTIVITY_RESULTS.json"
DEFAULT_MD = _REPO_ROOT / "PROVIDER_CONNECTIVITY_RESULTS.md"


def _safe_error(value: Any) -> str:
    text = str(value or "").strip()
    return text[:200]


def _safe_type(value: Any) -> str:
    return type(value).__name__ if value is not None else ""


def _proxy_detected() -> bool:
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    return any(str(os.environ.get(key) or "").strip() for key in keys)


def _dns_resolution_ok(hosts: List[str]) -> bool:
    for host in hosts:
        if not str(host or "").strip():
            continue
        try:
            socket.getaddrinfo(host, 443)
            return True
        except Exception:
            continue
    return False


async def collect_provider_connectivity(*, base_url: str = "", timeout: float = 20.0) -> Dict[str, Any]:
    settings = get_settings()
    search_query = "Relyce Infotech Founder CEO"
    extract_url = "https://www.microsoft.com/en-us/about"
    linkedin_extract_reference = "https://www.linkedin.com/company/relyce-infotech"
    diagnostics: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "search_provider_name": "serper",
        "extract_provider_name": "web_extract",
        "search_api_key_present": bool(settings.serper_api_key),
        "search_endpoint_configured": bool(str(settings.serper_base_url or "").strip()),
        "live_provider_configured": bool(settings.serper_api_key),
        "search_provider_ready": False,
        "extract_provider_ready": False,
        "external_network_ready": False,
        "dns_resolution_ok": False,
        "proxy_detected": _proxy_detected(),
        "provider_mode": "live" if base_url else "direct",
        "last_provider_error_safe": "",
        "search_http_status": None,
        "search_error_type": "",
        "search_error_safe": "",
        "search_query": search_query,
        "search_query_known": "who is the ceo of microsoft",
        "extract_url": extract_url,
        "linkedin_extract_reference": linkedin_extract_reference,
        "search_results_count": 0,
        "search_domains": [],
        "search_results": [],
        "search_known_results_count": 0,
        "search_known_domains": [],
        "search_known_results": [],
        "search_request_debug": {},
        "search_known_request_debug": {},
        "extract_status": "not_attempted",
        "extract_adapter": "",
        "extract_error_type": "",
        "extract_error_safe": "",
        "linkedin_snippet_preservation": {},
    }
    diagnostics["dns_resolution_ok"] = _dns_resolution_ok(
        [
            urlparse(str(settings.serper_base_url or "https://google.serper.dev")).netloc.replace("www.", ""),
            "www.microsoft.com",
            "www.linkedin.com",
        ]
    )

    try:
        search_res = await asyncio.wait_for(
            web_search(query=search_query, num_results=5, search_type="search"),
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover
        diagnostics["last_provider_error_safe"] = _safe_error(exc)
        diagnostics["search_error_type"] = _safe_type(exc)
        diagnostics["search_error_safe"] = _safe_error(exc)
        search_res = {"error": f"{type(exc).__name__}", "error_type": _safe_type(exc), "results": [], "provider_health": provider_health_snapshot()}
    search_results = list(search_res.get("results") or [])
    diagnostics["search_results_count"] = len(search_results)
    diagnostics["search_http_status"] = search_res.get("search_http_status")
    diagnostics["search_results"] = [
        {
            "title": str(row.get("title") or "").strip(),
            "url": str(row.get("link") or "").strip(),
            "domain": urlparse(str(row.get("link") or "")).netloc.replace("www.", ""),
            "snippet": str(row.get("snippet") or "").strip()[:280],
        }
        for row in search_results[:5]
    ]
    diagnostics["search_domains"] = sorted(
        {
            urlparse(str(row.get("link") or "")).netloc.replace("www.", "")
            for row in search_results
            if str(row.get("link") or "").strip()
        }
    )
    diagnostics["search_provider_ready"] = not bool(search_res.get("error"))
    diagnostics["search_request_debug"] = _dict(search_res.get("request_debug"))
    diagnostics["search_api_key_present"] = bool(search_res.get("search_api_key_present", diagnostics["search_api_key_present"]))
    diagnostics["search_endpoint_configured"] = bool(search_res.get("search_endpoint_configured", diagnostics["search_endpoint_configured"]))
    diagnostics["search_error_type"] = str(search_res.get("error_type") or diagnostics["search_error_type"] or "").strip()
    diagnostics["search_error_safe"] = str(search_res.get("error_safe") or search_res.get("error") or diagnostics["search_error_safe"] or "").strip()[:200]
    if search_res.get("error") and not diagnostics["last_provider_error_safe"]:
        diagnostics["last_provider_error_safe"] = _safe_error(search_res.get("error"))

    try:
        known_search = await asyncio.wait_for(
            web_search(query=diagnostics["search_query_known"], num_results=5, search_type="search"),
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover
        known_search = {"error": f"{type(exc).__name__}", "results": []}
    known_results = list(known_search.get("results") or [])
    diagnostics["search_known_results_count"] = len(known_results)
    diagnostics["search_known_results"] = [
        {
            "title": str(row.get("title") or "").strip(),
            "url": str(row.get("link") or "").strip(),
            "domain": urlparse(str(row.get("link") or "")).netloc.replace("www.", ""),
            "snippet": str(row.get("snippet") or "").strip()[:280],
        }
        for row in known_results[:5]
    ]
    diagnostics["search_known_domains"] = sorted(
        {
            urlparse(str(row.get("link") or "")).netloc.replace("www.", "")
            for row in known_results
            if str(row.get("link") or "").strip()
        }
    )
    diagnostics["search_known_request_debug"] = _dict(known_search.get("request_debug"))

    try:
        extract_res = await asyncio.wait_for(
            extract_with_adapter(
                url="https://www.microsoft.com/en-us/about",
                timeout=8,
                max_chars=1800,
                include_html=False,
                prefer_scrapling_http=bool(settings.scrapling_http_extractor_enabled),
            ),
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover
        extract_res = {"success": False, "error": f"{type(exc).__name__}"}
    diagnostics["extract_status"] = (
        "success"
        if bool(extract_res.get("success"))
        else "blocked"
        if any(token in str(extract_res.get("error") or extract_res.get("adapter_fallback_reason") or "").lower() for token in ("login", "blocked", "sign in"))
        else "failed"
    )
    diagnostics["extract_provider_ready"] = bool(extract_res.get("success"))
    diagnostics["extract_adapter"] = str(extract_res.get("extractor_adapter") or "").strip()
    diagnostics["extract_error_type"] = str(extract_res.get("error_type") or "").strip()
    diagnostics["extract_error_safe"] = _safe_error(extract_res.get("error") or extract_res.get("adapter_fallback_reason"))
    if not diagnostics["last_provider_error_safe"]:
        diagnostics["last_provider_error_safe"] = diagnostics["extract_error_safe"]

    provider_health = provider_health_snapshot()
    diagnostics["provider_health"] = provider_health
    combined_error = " ".join(
        [
            diagnostics["last_provider_error_safe"],
            diagnostics["extract_error_safe"],
            _safe_error(_dict(provider_health.get("serper")).get("last_error")),
            _safe_error(_dict(provider_health.get("web_extract")).get("last_error")),
        ]
    ).strip()
    diagnostics["external_network_ready"] = not any(
        token in combined_error.lower() for token in ("connect", "resolve", "dns", "network")
    )

    engine = OrchestrationEngine()
    ranked = engine._rank_entity_lookup_candidates(
        rows=[
            {
                "title": "Relyce Infotech LinkedIn company post",
                "link": "https://linkedin.com/company/relyce-infotech/posts/123",
                "snippet": "Core Team: Ukenthiran A Founder & CEO of Relyce infotech | Dharsan L | Tamizharuvi p | ...",
                "query": 'site:linkedin.com/posts/relyce-infotech Founder CEO',
                "query_lane": "linkedin",
            }
        ],
        role="ceo",
        entity="Relyce Infotech",
        limit=3,
    )
    top = ranked[0] if ranked else {}
    diagnostics["linkedin_snippet_preservation"] = {
        "candidate_name": str(top.get("role_holder_detected") or ""),
        "supported_role": str(top.get("supported_role") or ""),
        "role_match": bool(top.get("role_match")),
        "company_match": bool(top.get("company_match")),
        "target_entity_match": bool(top.get("target_entity_match")),
    }
    return diagnostics


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = [
        "# Provider Connectivity Results",
        "",
        f"- Generated: {report.get('timestamp')}",
        f"- search_provider_ready: `{report.get('search_provider_ready')}`",
        f"- extract_provider_ready: `{report.get('extract_provider_ready')}`",
        f"- external_network_ready: `{report.get('external_network_ready')}`",
        f"- search_results_count: `{report.get('search_results_count')}`",
        f"- extract_status: `{report.get('extract_status')}`",
        f"- last_provider_error_safe: `{report.get('last_provider_error_safe') or '-'}`",
        "",
        "## Search request debug",
        f"- method: `{_dict(report.get('search_request_debug')).get('method') or '-'}`",
        f"- endpoint_host: `{_dict(report.get('search_request_debug')).get('endpoint_host') or '-'}`",
        f"- endpoint_path: `{_dict(report.get('search_request_debug')).get('endpoint_path') or '-'}`",
        f"- body_keys: `{', '.join(_dict(report.get('search_request_debug')).get('body_keys') or []) or '-'}`",
        f"- has_q: `{_dict(report.get('search_request_debug')).get('has_q')}`",
        f"- content_type: `{_dict(report.get('search_request_debug')).get('content_type') or '-'}`",
        f"- auth_header_present: `{_dict(report.get('search_request_debug')).get('auth_header_present')}`",
        f"- response_error_preview: `{_dict(report.get('search_request_debug')).get('response_error_preview') or '-'}`",
        "",
        "## Search results",
    ]
    for row in report.get("search_results") or []:
        lines.extend(
            [
                f"- {row.get('domain') or '-'}",
                f"  - title: {row.get('title') or '-'}",
                f"  - url: {row.get('url') or '-'}",
                f"  - snippet: {row.get('snippet') or '-'}",
            ]
        )
    lines.extend(["", "## Known search smoke"])
    for row in report.get("search_known_results") or []:
        lines.extend(
            [
                f"- {row.get('domain') or '-'}",
                f"  - title: {row.get('title') or '-'}",
                f"  - url: {row.get('url') or '-'}",
                f"  - snippet: {row.get('snippet') or '-'}",
            ]
        )
    lines.extend(
        [
            "",
            "## LinkedIn snippet preservation",
            f"- candidate_name: `{_dict(report.get('linkedin_snippet_preservation')).get('candidate_name') or '-'}`",
            f"- supported_role: `{_dict(report.get('linkedin_snippet_preservation')).get('supported_role') or '-'}`",
            f"- role_match: `{_dict(report.get('linkedin_snippet_preservation')).get('role_match')}`",
            "",
            "## Provider config",
            f"- search_api_key_present: `{report.get('search_api_key_present')}`",
            f"- search_endpoint_configured: `{report.get('search_endpoint_configured')}`",
            f"- search_http_status: `{report.get('search_http_status')}`",
            f"- search_error_type: `{report.get('search_error_type') or '-'}`",
            f"- extract_error_type: `{report.get('extract_error_type') or '-'}`",
            f"- dns_resolution_ok: `{report.get('dns_resolution_ok')}`",
            f"- proxy_detected: `{report.get('proxy_detected')}`",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run provider connectivity diagnostics for TAOS live evidence retrieval.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out-json", default=str(DEFAULT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_MD))
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    report = asyncio.run(collect_provider_connectivity(base_url=args.base_url, timeout=args.timeout))
    Path(args.out_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    Path(args.out_md).write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
