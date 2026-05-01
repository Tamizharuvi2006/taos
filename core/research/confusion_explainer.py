from __future__ import annotations

from typing import Any, Dict, Iterable, List


class ConfusionExplainer:
    def explain(
        self,
        rows: Iterable[Dict[str, Any]] | None = None,
        *,
        query: str = "",
        source_rows: Iterable[Dict[str, Any]] | None = None,
    ) -> List[str]:
        evidence_rows = list(rows or source_rows or [])
        text = " ".join(
            str(row.get(key) or "")
            for row in evidence_rows
            for key in ("title", "snippet", "summary", "raw_snippet")
        ).lower()
        hints: List[str] = []
        if "claude" in str(query or "").lower():
            if any(token in text for token in ("mythos", "cybersecurity", "risk", "rbi", "banks")):
                hints.append("Claude Mythos cybersecurity-risk or regulator-review news")
            if "outage" in text:
                hints.append("Claude outage or access-issue stories")
            if "account" in text or "suspend" in text:
                hints.append("account suspension or access-review stories")
            if "supported countries" in text or "available in india" in text:
                hints.append("availability/support-page context being mixed with rumour claims")
        if "outage" in text and all("outage" not in hint for hint in hints):
            hints.append("outage or temporary service-issue coverage")
        if "security" in text or "cyber" in text:
            hints.append("security or risk-review coverage")
        if "account" in text or "suspend" in text:
            hints.append("account suspension or access-review stories")
        if not hints:
            hints.append("Related policy, outage, or risk-review stories may be getting mixed into the exact claim.")
        return hints
