"""
TAOS extraction adapter.

Feature-flagged Scrapling adapter with policy-driven mode selection:
- HTTP (fast/static)
- Dynamic (browser-rendered pages)
- Stealthy (protected/challenge-heavy pages)

Falls back to existing `web_extract` while preserving normalized schema.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from taos.core.tools.builtin.web_extract import (
    _assess_extraction_quality,
    _clean_html_to_text,
    _classify_page_type,
    _extract_author,
    _extract_paragraph_text,
    _extract_published_at,
    _extract_title,
)
from taos.core.tools.builtin import web_extract as _web_extract_module


def _parse_domain(url: str) -> str:
    try:
        parsed = urlparse(str(url or "").strip())
        return (parsed.netloc or "").lower()
    except Exception:
        return ""


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value


def _pick_first_attr(obj: Any, attrs: List[str]) -> Any:
    for name in attrs:
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _get_fetchers_module() -> Any:
    return importlib.import_module("scrapling.fetchers")


async def _try_call_method(method: Any, *, url: str, timeout: int) -> Any:
    call_patterns = (
        {"url": url, "timeout": timeout},
        {"url": url, "request_timeout": timeout},
        {"url": url},
    )
    for kwargs in call_patterns:
        try:
            return await _maybe_await(method(**kwargs))
        except TypeError:
            continue
    for args in ((url, timeout), (url,)):
        try:
            return await _maybe_await(method(*args))
        except TypeError:
            continue
    return None


def _try_call_method_sync(method: Any, *, url: str, timeout: int) -> Any:
    call_patterns = (
        {"url": url, "timeout": timeout},
        {"url": url, "request_timeout": timeout},
        {"url": url},
    )
    for kwargs in call_patterns:
        try:
            return method(**kwargs)
        except TypeError:
            continue
    for args in ((url, timeout), (url,)):
        try:
            return method(*args)
        except TypeError:
            continue
    return None


async def _fetch_with_class(fetcher_class: Any, *, url: str, timeout: int) -> Any:
    if fetcher_class is None:
        return None
    # Static helper methods.
    for method_name in ("get", "fetch", "request"):
        method = getattr(fetcher_class, method_name, None)
        if method is None:
            continue
        response = await _try_call_method(method, url=url, timeout=timeout)
        if response is not None:
            return response
    # Instance methods.
    try:
        fetcher = fetcher_class()
    except Exception:
        return None
    for method_name in ("get", "fetch", "request"):
        method = getattr(fetcher, method_name, None)
        if method is None:
            continue
        response = await _try_call_method(method, url=url, timeout=timeout)
        if response is not None:
            return response
    return None


async def _fetch_html_via_scrapling(*, url: str, timeout: int, mode: str) -> Any:
    """
    D4Vinci Scrapling integration.
    mode:
      - http: AsyncFetcher/Fetcher
      - dynamic: DynamicFetcher
      - stealth: StealthyFetcher
    """
    fetchers = _get_fetchers_module()
    mode_key = str(mode or "http").strip().lower()

    if mode_key == "dynamic":
        klass = getattr(fetchers, "DynamicFetcher", None)
        return await _fetch_with_class(klass, url=url, timeout=timeout)
    if mode_key == "stealth":
        klass = getattr(fetchers, "StealthyFetcher", None)
        return await _fetch_with_class(klass, url=url, timeout=timeout)

    # HTTP mode.
    async_fetcher = getattr(fetchers, "AsyncFetcher", None)
    if async_fetcher is not None:
        for method_name in ("get", "fetch", "request"):
            method = getattr(async_fetcher, method_name, None)
            if method is None:
                continue
            response = await _try_call_method(method, url=url, timeout=timeout)
            if response is not None:
                return response

    sync_fetcher = getattr(fetchers, "Fetcher", None)
    if sync_fetcher is not None:
        for method_name in ("get", "fetch", "request"):
            method = getattr(sync_fetcher, method_name, None)
            if method is None:
                continue
            response = await asyncio.to_thread(
                lambda m=method: _try_call_method_sync(m, url=url, timeout=timeout)
            )
            if response is not None:
                return response
    return None


def _normalize_scrapling_response(
    *,
    response: Any,
    request_url: str,
    max_chars: int,
    include_html: bool,
    adapter_name: str,
) -> Dict[str, Any]:
    final_url = str(
        _pick_first_attr(response, ["url", "final_url", "requested_url"]) or request_url
    )
    status_code = int(_pick_first_attr(response, ["status_code", "status"]) or 200)
    content_type = str(
        _pick_first_attr(response, ["content_type", "headers_content_type", "mime_type"])
        or "text/html"
    ).lower()

    raw_html = _pick_first_attr(response, ["html", "text", "content", "body"])
    if raw_html is None:
        raw_html = _pick_first_attr(response, ["raw_html", "markup", "source"])
    if isinstance(raw_html, bytes):
        html = raw_html.decode("utf-8", errors="ignore")
    else:
        html = str(raw_html or "")
    if not html:
        page_html_method = _pick_first_attr(response, ["to_html", "rendered_html"])
        if callable(page_html_method):
            try:
                maybe_html = page_html_method()
                html = str(maybe_html or "")
            except Exception:
                html = ""
    if not html:
        html = str(response or "")

    title = _extract_title(html)
    published_at = _extract_published_at(html)
    author = _extract_author(html)
    paragraph_text = _extract_paragraph_text(html)
    text = paragraph_text if len(paragraph_text) >= 220 else _clean_html_to_text(html)
    text = text[:max_chars]

    page_type = _classify_page_type(final_url, title, html)
    quality_meta = _assess_extraction_quality(
        page_type=page_type,
        text=text,
        title=title,
        published_at=published_at,
        author=author,
        html=html,
    )

    success = 200 <= status_code < 400 and bool(text) and bool(quality_meta["usable_for_research"])
    out: Dict[str, Any] = {
        "success": success,
        "status_code": status_code,
        "url": final_url,
        "domain": _parse_domain(final_url),
        "page_type": page_type,
        "title": title,
        "published_at": published_at,
        "author": author,
        "text": text,
        "text_length": len(text),
        "content_type": content_type or "text/html",
        "extraction_quality": quality_meta["quality"],
        "quality_score": quality_meta["quality_score"],
        "usable_for_research": quality_meta["usable_for_research"],
        "rejection_reason": quality_meta["rejection_reason"],
        "noise_ratio": quality_meta["noise_ratio"],
        "extractor_adapter": adapter_name,
    }
    if not success:
        out["error"] = (
            f"scrapling_normalized_unsuccessful"
            f" status={status_code}"
            f" text_len={len(text)}"
            f" quality={quality_meta['quality_score']:.2f}"
            f" rejection={quality_meta['rejection_reason'] or 'none'}"
        )
    if include_html:
        out["html"] = html[:12000]
    return out


async def _extract_with_scrapling_mode(
    *,
    url: str,
    timeout: int,
    max_chars: int,
    include_html: bool,
    mode: str,
) -> Dict[str, Any]:
    mode_key = str(mode or "http").strip().lower()
    adapter_name = {
        "http": "scrapling_http_d4vinci",
        "dynamic": "scrapling_dynamic_d4vinci",
        "stealth": "scrapling_stealth_d4vinci",
    }.get(mode_key, "scrapling_http_d4vinci")
    response = await _fetch_html_via_scrapling(url=url, timeout=timeout, mode=mode_key)
    if response is None:
        return {
            "success": False,
            "extractor_adapter": adapter_name,
            "error": f"{adapter_name}_no_response",
            "adapter_mode": mode_key,
        }
    normalized = _normalize_scrapling_response(
        response=response,
        request_url=url,
        max_chars=max_chars,
        include_html=include_html,
        adapter_name=adapter_name,
    )
    normalized["adapter_mode"] = mode_key
    return normalized


async def _fallback_web_extract(
    *,
    url: str,
    timeout: int,
    max_chars: int,
    include_html: bool,
    attempted_adapters: List[str],
    fallback_reason: Optional[str],
) -> Dict[str, Any]:
    fallback = await _web_extract_module.web_extract(
        url=url,
        timeout=timeout,
        max_chars=max_chars,
        include_html=include_html,
    )
    if isinstance(fallback, dict):
        fallback.setdefault("extractor_adapter", "web_extract")
        fallback["attempted_adapters"] = attempted_adapters
        if fallback_reason:
            fallback["adapter_fallback_reason"] = str(fallback_reason)[:240]
    return fallback


async def extract_with_adapter(
    *,
    url: str,
    timeout: int = 15,
    max_chars: int = 6000,
    include_html: bool = False,
    prefer_scrapling_http: bool = False,
    adapter_preference: str = "auto",
    dynamic_enabled: bool = False,
    stealth_enabled: bool = False,
    dynamic_timeout_ms: Optional[int] = None,
    stealth_timeout_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Feature-flagged extraction path.

    adapter_preference:
      - auto|http|dynamic|stealth
    """
    mode = str(adapter_preference or "auto").strip().lower()
    want_scrapling = bool(
        prefer_scrapling_http or mode in {"http", "dynamic", "stealth", "auto"}
    )
    if not want_scrapling:
        return await _fallback_web_extract(
            url=url,
            timeout=timeout,
            max_chars=max_chars,
            include_html=include_html,
            attempted_adapters=["web_extract"],
            fallback_reason=None,
        )

    attempted_adapters: List[str] = []
    fallback_reason: Optional[str] = None

    if mode in {"dynamic", "stealth"} and not prefer_scrapling_http:
        # Guard: explicitly requested advanced mode but base flag not enabled.
        mode = "http"

    candidate_modes: List[str] = []
    if mode in {"http", "dynamic", "stealth"}:
        candidate_modes = [mode]
    else:
        candidate_modes = ["http"]

    if "dynamic" in candidate_modes and not dynamic_enabled:
        fallback_reason = "dynamic_fetcher_disabled"
        candidate_modes = [m for m in candidate_modes if m != "dynamic"]
    if "stealth" in candidate_modes and not stealth_enabled:
        fallback_reason = "stealth_fetcher_disabled"
        candidate_modes = [m for m in candidate_modes if m != "stealth"]
    if not candidate_modes:
        candidate_modes = ["http"]

    for candidate_mode in candidate_modes:
        adapter_name = {
            "http": "scrapling_http_d4vinci",
            "dynamic": "scrapling_dynamic_d4vinci",
            "stealth": "scrapling_stealth_d4vinci",
        }.get(candidate_mode, "scrapling_http_d4vinci")
        attempted_adapters.append(adapter_name)
        try:
            mode_timeout = timeout
            if candidate_mode == "dynamic" and dynamic_timeout_ms:
                mode_timeout = max(1, int(dynamic_timeout_ms // 1000))
            elif candidate_mode == "stealth" and stealth_timeout_ms:
                mode_timeout = max(1, int(stealth_timeout_ms // 1000))
            result = await _extract_with_scrapling_mode(
                url=url,
                timeout=mode_timeout,
                max_chars=max_chars,
                include_html=include_html,
                mode=candidate_mode,
            )
            if result.get("success"):
                result["attempted_adapters"] = attempted_adapters
                return result
            if not fallback_reason:
                fallback_reason = str(result.get("error") or f"{adapter_name}_failed")
        except ModuleNotFoundError:
            if not fallback_reason:
                fallback_reason = (
                    "fetcher_unavailable: install scrapling fetchers extras "
                    "(pip install \"scrapling[fetchers]\" && scrapling install)"
                )
        except Exception as exc:
            if not fallback_reason:
                fallback_reason = f"{type(exc).__name__}: {exc}"

    return await _fallback_web_extract(
        url=url,
        timeout=timeout,
        max_chars=max_chars,
        include_html=include_html,
        attempted_adapters=attempted_adapters + ["web_extract"],
        fallback_reason=fallback_reason,
    )
