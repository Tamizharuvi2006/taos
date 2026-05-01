from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlparse


class SourceQualityScorer:
    def score(self, row: Dict[str, Any]) -> Dict[str, Any]:
        enriched = dict(row or {})
        domain = self._domain(enriched)
        tier = str(enriched.get("tier") or "other").lower()
        authority = 0.95 if tier == "official" else 0.75 if tier == "trusted" else 0.45
        freshness = float(enriched.get("freshness_score") or 0.0)
        relevance = min(1.0, 0.4 + (0.15 if enriched.get("snippet") else 0.0) + (0.15 if enriched.get("title") else 0.0))
        extraction_quality = float(enriched.get("extract_quality_score") or 0.0)
        spam_penalty = 0.18 if "quora.com" in domain else 0.0
        duplicate_penalty = float(enriched.get("duplicate_penalty") or 0.0)
        diversity = 0.1 if domain else 0.0
        score = authority + freshness + relevance + diversity + extraction_quality - spam_penalty - duplicate_penalty
        enriched["domain"] = domain
        enriched["source_score"] = round(max(0.0, min(1.0, score / 3.3)), 3)
        enriched["reason"] = self._reason(tier=tier, freshness=freshness, domain=domain)
        return enriched

    def diversify(self, rows: List[Dict[str, Any]], *, max_per_domain: int = 2) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        counts: Dict[str, int] = {}
        for row in sorted(rows, key=lambda item: float(item.get("source_score") or 0.0), reverse=True):
            domain = self._domain(row) or "unknown"
            seen = counts.get(domain, 0)
            if seen >= max(1, int(max_per_domain or 2)):
                continue
            counts[domain] = seen + 1
            out.append(row)
        return out

    def _domain(self, row: Dict[str, Any]) -> str:
        raw = str(row.get("domain") or row.get("provider") or "").strip()
        if raw:
            return raw.lower()
        url = str(row.get("link") or row.get("url") or "").strip()
        if not url:
            return ""
        return urlparse(url).netloc.lower().replace("www.", "")

    def _reason(self, *, tier: str, freshness: float, domain: str) -> str:
        if tier == "official":
            return "Official source, recent, directly relevant" if freshness >= 0.5 else "Official source, directly relevant"
        if tier == "trusted":
            return "Trusted source with usable evidence"
        return f"General source from {domain or 'mixed web'}"
