from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, Iterable, List

from .source_diversity_enforcer import SourceDiversityEnforcer, source_domain


class EvidenceSelector:
    """Ranks and trims evidence rows before synthesis."""

    def __init__(self) -> None:
        self._diversity = SourceDiversityEnforcer()

    def select(
        self,
        rows: Iterable[Dict[str, Any]],
        *,
        query: str = "",
        limit: int = 8,
        max_per_domain: int = 2,
    ) -> Dict[str, Any]:
        scored: List[Dict[str, Any]] = []
        seen_fingerprints: Counter[str] = Counter()
        query_tokens = self._tokens(query)

        for index, row in enumerate(rows or []):
            item = dict(row)
            fingerprint = self._fingerprint(item)
            seen_fingerprints[fingerprint] += 1
            score, reasons = self._score_row(
                item,
                query_tokens=query_tokens,
                duplicate_count=seen_fingerprints[fingerprint],
            )
            item["selection_score"] = round(score, 3)
            item["selection_reasons"] = reasons
            item["source_domain"] = source_domain(item)
            item["_original_index"] = index
            scored.append(item)

        scored.sort(key=lambda row: (-float(row.get("selection_score") or 0.0), int(row.get("_original_index") or 0)))
        diversified = self._diversity.enforce(scored, max_per_domain=max_per_domain, limit=limit)
        selected = list(diversified.get("rows") or [])
        summary = dict(diversified.get("summary") or {})
        summary.update(
            {
                "candidate_count": len(scored),
                "selected_count": len(selected),
                "avg_selection_score": round(
                    sum(float(row.get("selection_score") or 0.0) for row in selected) / max(1, len(selected)),
                    3,
                ),
                "snippet_only_count": sum(1 for row in selected if not row.get("extract_quality_score")),
                "duplicate_candidates": sum(count - 1 for count in seen_fingerprints.values() if count > 1),
            }
        )
        for row in selected:
            row.pop("_original_index", None)
        return {"rows": selected, "summary": summary}

    def _score_row(self, row: Dict[str, Any], *, query_tokens: set[str], duplicate_count: int) -> tuple[float, List[str]]:
        reasons: List[str] = []
        text = " ".join(str(row.get(key) or "") for key in ("title", "snippet", "summary", "provider", "domain")).lower()
        row_tokens = self._tokens(text)
        relevance = len(query_tokens & row_tokens) / max(1, len(query_tokens)) if query_tokens else 0.35
        source_quality = float(row.get("source_quality") or row.get("quality_score") or row.get("score") or 0.55)
        if str(row.get("tier") or "").lower() == "official":
            source_quality = max(source_quality, 0.9)
            reasons.append("official_source")
        freshness = float(row.get("freshness_score") or (0.75 if row.get("date_hint") or row.get("published_at") else 0.45))
        extraction = float(row.get("extract_quality_score") or 0.0)
        claim_density = min(1.0, len(re.findall(r"\b(?:\d{4}|[A-Z][a-z]{2,}|percent|%|version|policy)\b", text)) / 8.0)
        uniqueness = 1.0 if duplicate_count <= 1 else max(0.25, 1.0 / duplicate_count)
        snippet_penalty = 0.12 if not extraction else 0.0
        duplicate_penalty = 0.18 if duplicate_count > 1 else 0.0
        score = (
            relevance * 0.26
            + source_quality * 0.22
            + freshness * 0.16
            + extraction * 0.14
            + claim_density * 0.1
            + uniqueness * 0.12
            - snippet_penalty
            - duplicate_penalty
        )
        if relevance >= 0.45:
            reasons.append("query_relevant")
        if freshness >= 0.75:
            reasons.append("fresh")
        if extraction >= 0.65:
            reasons.append("good_extraction")
        if duplicate_count > 1:
            reasons.append("duplicate_penalty")
        if snippet_penalty:
            reasons.append("snippet_only_penalty")
        return max(0.0, min(1.0, score)), reasons

    def _tokens(self, text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]{3,}", str(text or "").lower())}

    def _fingerprint(self, row: Dict[str, Any]) -> str:
        link = str(row.get("link") or row.get("url") or "").strip().lower()
        if link:
            return link.split("#")[0].rstrip("/")
        return re.sub(r"\s+", " ", str(row.get("title") or row.get("snippet") or "").lower())[:120]
