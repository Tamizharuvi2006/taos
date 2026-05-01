from __future__ import annotations

from collections import Counter
import re
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse


LOW_VALUE_PATH_MARKERS = (
    "/search",
    "/tag/",
    "/tags/",
    "/category/",
    "/categories/",
    "/topics/",
    "/topic/",
    "/login",
    "/signin",
    "/auth",
)


class ResultPrefilter:
    def filter(
        self,
        *,
        rows: Iterable[Dict[str, Any]],
        primary_subject: str = "",
        disallowed_subject_drifts: Iterable[str] = (),
    ) -> Dict[str, Any]:
        kept: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        seen = set()
        reasons: Counter[str] = Counter()
        drift_terms = [str(item).lower() for item in disallowed_subject_drifts if str(item).strip()]
        subject = str(primary_subject or "").strip().lower()

        for raw in rows or []:
            row = dict(raw or {})
            url = str(row.get("link") or row.get("url") or "").strip()
            domain = str(row.get("domain") or "").strip().lower() or urlparse(url).netloc.lower()
            text = " ".join(
                str(row.get(key) or "")
                for key in ("title", "snippet", "summary", "raw_snippet", "search_snippet")
            ).lower()
            signature = (url.lower(), re.sub(r"\s+", " ", text)[:160])
            reason = None
            if signature in seen:
                reason = "duplicate"
            elif any(marker in url.lower() for marker in LOW_VALUE_PATH_MARKERS):
                reason = "low_value_page"
            elif any(marker in text for marker in ("seo", "best hosting", "top providers", "compare plans")) and subject == "claude":
                reason = "seo_spam"
            elif drift_terms and any(term in text for term in drift_terms) and subject and subject not in text:
                reason = "subject_drift"
            elif not url and len(re.findall(r"[a-z0-9]{4,}", text)) < 5:
                reason = "low_relevance"
            if reason:
                row["prefilter_reason"] = reason
                rejected.append(row)
                reasons[reason] += 1
                continue
            seen.add(signature)
            row["domain"] = domain
            kept.append(row)
        return {
            "kept": kept,
            "rejected": rejected,
            "rejected_results_count": len(rejected),
            "reasons": dict(reasons),
        }
