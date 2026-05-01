"""
TAOS Built-in Tool: Web Extract.

Fetches a web page and extracts readable text + lightweight metadata.
Designed for research quality improvements over raw snippet-only search.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from taos.config.constants import ToolRiskLevel
from taos.core.reliability.provider_health import GLOBAL_PROVIDER_HEALTH, provider_health_snapshot
from taos.core.reliability.provider_policy import get_provider_policy
from taos.core.tools.registry import ToolDefinition, ToolPolicy


def _clean_html_to_text(html: str) -> str:
    text = html or ""
    # Remove script/style/svg/noscript blocks.
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<svg[^>]*>.*?</svg>", " ", text)
    text = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", text)
    # Strip tags and collapse whitespace.
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _extract_paragraph_text(html: str) -> str:
    paragraphs = re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", html or "")
    parts: List[str] = []
    for raw in paragraphs:
        cleaned = _clean_html_to_text(raw)
        if len(cleaned) >= 40:
            parts.append(cleaned)
    return "\n".join(parts).strip()


def _extract_title(html: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    return unescape(m.group(1)).strip() if m else ""


def _extract_meta_content(html: str, key: str) -> str:
    # Match both name="" and property="" style meta tags.
    patterns = [
        rf'(?is)<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\'](.*?)["\']',
        rf'(?is)<meta[^>]+property=["\']{re.escape(key)}["\'][^>]+content=["\'](.*?)["\']',
    ]
    for p in patterns:
        m = re.search(p, html or "")
        if m:
            return unescape(m.group(1)).strip()
    return ""


def _extract_published_at(html: str) -> str:
    keys = (
        "article:published_time",
        "og:published_time",
        "article:modified_time",
        "og:updated_time",
        "publishdate",
        "pubdate",
        "datePublished",
        "date",
    )
    for k in keys:
        v = _extract_meta_content(html, k)
        if v:
            return v
    # time datetime fallback.
    m = re.search(r'(?is)<time[^>]+datetime=["\']([^"\']+)["\']', html or "")
    if m:
        return m.group(1).strip()
    # JSON-LD fallback.
    m = re.search(r'(?is)"datePublished"\s*:\s*"([^"]+)"', html or "")
    if m:
        return m.group(1).strip()
    return ""


def _extract_author(html: str) -> str:
    keys = (
        "author",
        "article:author",
        "og:article:author",
        "twitter:creator",
    )
    for k in keys:
        v = _extract_meta_content(html, k)
        if v:
            return v
    m = re.search(r'(?is)"author"\s*:\s*"([^"]+)"', html or "")
    if m:
        return m.group(1).strip()
    return ""


def _classify_page_type(url: str, title: str, html: str) -> str:
    lower_url = (url or "").lower()
    lower_title = (title or "").lower()
    lower_html = (html or "").lower()
    if any(token in lower_url for token in ("/search", "?q=", "/tag/", "/tags/", "/category/", "/topics/")):
        return "index"
    if any(token in lower_url for token in ("/login", "/signin", "/sign-in", "/account", "/subscribe", "/paywall")):
        return "gated"
    if "404" in lower_title or "not found" in lower_title:
        return "error"
    if "captcha" in lower_html or "access denied" in lower_html:
        return "blocked"
    if "<article" in lower_html:
        return "article"
    return "webpage"


def _nav_noise_ratio(text: str) -> float:
    if not text:
        return 1.0
    noise_terms = (
        "cookie",
        "privacy policy",
        "terms of service",
        "sign in",
        "log in",
        "subscribe",
        "all rights reserved",
    )
    lowered = text.lower()
    hits = sum(1 for token in noise_terms if token in lowered)
    return min(1.0, float(hits) / float(max(1, len(noise_terms))))


def _quality_bucket(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.5:
        return "moderate"
    if score >= 0.3:
        return "low"
    return "poor"


def _assess_extraction_quality(
    *,
    page_type: str,
    text: str,
    title: str,
    published_at: str,
    author: str,
    html: str,
) -> Dict[str, Any]:
    text_length = len(text or "")
    noise_ratio = _nav_noise_ratio(text)
    has_article_tag = "<article" in (html or "").lower()
    score = 0.0
    if text_length >= 1200:
        score += 0.50
    elif text_length >= 700:
        score += 0.40
    elif text_length >= 350:
        score += 0.26
    elif text_length >= 180:
        score += 0.14
    else:
        score += 0.05

    if title:
        score += 0.10
    if published_at:
        score += 0.10
    if author:
        score += 0.05
    if has_article_tag:
        score += 0.10
    if noise_ratio <= 0.20:
        score += 0.10
    elif noise_ratio >= 0.50:
        score -= 0.15

    rejection_reason: Optional[str] = None
    if page_type in {"gated", "error", "blocked"}:
        score = min(score, 0.20)
        rejection_reason = f"page_type_{page_type}"
    elif page_type == "index":
        score = min(score, 0.30)
        rejection_reason = "index_like_page"
    elif text_length < 180:
        rejection_reason = "text_too_short"

    score = max(0.0, min(1.0, score))
    quality = _quality_bucket(score)
    usable_for_research = score >= 0.40 and page_type not in {"gated", "error", "blocked"} and text_length >= 180
    if not usable_for_research and rejection_reason is None:
        rejection_reason = "low_quality_content"
    return {
        "quality_score": round(score, 3),
        "quality": quality,
        "usable_for_research": usable_for_research,
        "rejection_reason": rejection_reason,
        "noise_ratio": round(float(noise_ratio), 3),
    }


async def web_extract(
    url: str,
    timeout: int = 15,
    max_chars: int = 6000,
    include_html: bool = False,
) -> Dict[str, Any]:
    """
    Fetch and extract readable text from a URL.

    Args:
        url: Target URL
        timeout: Request timeout in seconds
        max_chars: Max chars returned for extracted text
        include_html: Whether to include truncated raw HTML in output
    """
    if not url or not url.startswith(("http://", "https://")):
        return {"success": False, "error": f"Invalid URL: {url}"}
    if max_chars < 500:
        max_chars = 500
    if max_chars > 20000:
        max_chars = 20000

    policy = get_provider_policy("web_extract")
    if not GLOBAL_PROVIDER_HEALTH.allow_request("web_extract"):
        GLOBAL_PROVIDER_HEALTH.mark_fallback("web_extract")
        return {
            "success": False,
            "error": "Extraction provider circuit open",
            "error_type": "circuit_open",
            "url": url,
            "extraction_failed": True,
            "provider_name": "web_extract",
            "provider_health": provider_health_snapshot(),
        }

    try:
        async with httpx.AsyncClient(timeout=min(float(timeout or policy.timeout_seconds), policy.timeout_seconds), follow_redirects=True) as client:
            response = await client.get(url)

        content_type = (response.headers.get("content-type", "") or "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            return {
                "success": False,
                "error": f"Unsupported content-type for extraction: {content_type}",
                "error_type": "unsupported_content_type",
                "status_code": response.status_code,
                "url": str(response.url),
                "extraction_failed": True,
                "provider_name": "web_extract",
                "provider_health": provider_health_snapshot(),
            }

        html = response.text or ""
        title = _extract_title(html)
        published_at = _extract_published_at(html)
        author = _extract_author(html)
        paragraph_text = _extract_paragraph_text(html)
        text = paragraph_text if len(paragraph_text) >= 220 else _clean_html_to_text(html)
        text = text[:max_chars]

        parsed = urlparse(str(response.url))
        domain = parsed.netloc.lower()
        page_type = _classify_page_type(str(response.url), title, html)
        quality_meta = _assess_extraction_quality(
            page_type=page_type,
            text=text,
            title=title,
            published_at=published_at,
            author=author,
            html=html,
        )

        out: Dict[str, Any] = {
            "success": 200 <= response.status_code < 400 and bool(text),
            "status_code": response.status_code,
            "url": str(response.url),
            "domain": domain,
            "page_type": page_type,
            "title": title,
            "published_at": published_at,
            "author": author,
            "text": text,
            "text_length": len(text),
            "content_type": content_type,
            "extraction_quality": quality_meta["quality"],
            "quality_score": quality_meta["quality_score"],
            "usable_for_research": quality_meta["usable_for_research"],
            "rejection_reason": quality_meta["rejection_reason"],
            "noise_ratio": quality_meta["noise_ratio"],
            "provider_name": "web_extract",
        }
        if include_html:
            out["html"] = html[:12000]
        if out["success"]:
            GLOBAL_PROVIDER_HEALTH.record_success("web_extract")
        else:
            GLOBAL_PROVIDER_HEALTH.record_failure("web_extract", f"status_{response.status_code}")
            out["extraction_failed"] = True
        out["provider_health"] = provider_health_snapshot()
        return out
    except httpx.TimeoutException as exc:
        GLOBAL_PROVIDER_HEALTH.record_failure("web_extract", exc)
        GLOBAL_PROVIDER_HEALTH.mark_fallback("web_extract")
        return {"success": False, "error": f"Extraction timed out after {timeout}s", "error_type": "timeout", "url": url, "extraction_failed": True, "provider_name": "web_extract", "provider_health": provider_health_snapshot()}
    except Exception as e:
        GLOBAL_PROVIDER_HEALTH.record_failure("web_extract", e)
        GLOBAL_PROVIDER_HEALTH.mark_fallback("web_extract")
        return {"success": False, "error": f"Extraction failed: {str(e)}", "error_type": type(e).__name__, "url": url, "extraction_failed": True, "provider_name": "web_extract", "provider_health": provider_health_snapshot()}


def create_web_extract_tool() -> ToolDefinition:
    """Factory function for web extraction tool."""
    return ToolDefinition(
        name="web_extract",
        description=(
            "Fetch a web page URL and extract readable article text plus metadata "
            "(title, domain, published_at). Use this after web_search for evidence grounding."
        ),
        input_schema={
            "url": "str",
            "timeout": "int",
            "max_chars": "int",
            "include_html": "bool",
        },
        handler=web_extract,
        policy=ToolPolicy(
            allowed_roles=["agent"],
            max_calls_per_task=12,
            risk_level=ToolRiskLevel.LOW,
            audit_required=False,
        ),
        rate_limit=30,
        cost_estimate=0.0,
        timeout=20,
        tags=["web", "extract", "research", "reading"],
    )
