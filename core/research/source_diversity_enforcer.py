from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse


def source_domain(row: Dict[str, Any]) -> str:
    value = str(row.get("provider") or row.get("domain") or "").strip().lower()
    if value:
        return value.replace("www.", "")
    link = str(row.get("link") or row.get("url") or "").strip()
    if not link:
        return ""
    return str(urlparse(link).netloc or "").lower().replace("www.", "")


class SourceDiversityEnforcer:
    """Caps repeated domains and keeps a useful mix of source categories."""

    CATEGORY_ORDER = ("official", "primary", "technical", "trusted", "news", "balance", "other")

    def enforce(
        self,
        rows: Iterable[Dict[str, Any]],
        *,
        max_per_domain: int = 2,
        limit: int = 8,
    ) -> Dict[str, Any]:
        kept: List[Dict[str, Any]] = []
        domain_counts: Counter[str] = Counter()
        category_counts: Counter[str] = Counter()
        dropped_by_domain = 0

        ordered = sorted(
            [dict(row) for row in rows or []],
            key=lambda row: (
                self._category_rank(row),
                -float(row.get("selection_score") or row.get("score") or row.get("quality_score") or 0.0),
            ),
        )
        for row in ordered:
            domain = source_domain(row) or "unknown"
            if domain_counts[domain] >= max(1, int(max_per_domain or 2)):
                dropped_by_domain += 1
                continue
            category = self._category(row)
            row["source_domain"] = domain
            row["source_category"] = category
            kept.append(row)
            domain_counts[domain] += 1
            category_counts[category] += 1
            if len(kept) >= max(1, int(limit or 8)):
                break

        return {
            "rows": kept,
            "summary": {
                "kept": len(kept),
                "dropped_by_domain_cap": dropped_by_domain,
                "unique_domains": len(domain_counts),
                "max_per_domain": max_per_domain,
                "category_counts": dict(category_counts),
                "domain_counts": dict(domain_counts),
            },
        }

    def _category(self, row: Dict[str, Any]) -> str:
        tier = str(row.get("tier") or row.get("source_type") or "").strip().lower()
        provider = source_domain(row)
        title = str(row.get("title") or "").lower()
        text = f"{provider} {title}"
        if tier == "official" or any(token in text for token in ("gov", ".edu", "official", "docs.", "developer.", "sec.gov")):
            return "official"
        if any(token in text for token in ("press release", "company blog", "investor relations")):
            return "primary"
        if any(token in text for token in ("github", "documentation", "standards", "ietf", "w3c")):
            return "technical"
        if tier in {"trusted", "reputable"}:
            return "trusted"
        if any(token in text for token in ("reuters", "apnews", "bbc", "nytimes", "the hindu", "guardian")):
            return "news"
        if any(token in text for token in ("criticism", "concern", "risk", "controversy", "against")):
            return "balance"
        return "other"

    def _category_rank(self, row: Dict[str, Any]) -> int:
        category = self._category(row)
        try:
            return self.CATEGORY_ORDER.index(category)
        except ValueError:
            return len(self.CATEGORY_ORDER)
