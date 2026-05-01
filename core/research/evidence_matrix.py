"""
Evidence matrix utilities for claim-to-source support analysis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "is", "it", "of", "on", "or", "that", "the", "their", "this", "to", "was",
    "were", "will", "with", "you", "your", "i", "we", "they", "he", "she", "its",
}


@dataclass
class EvidenceRow:
    claim: str
    support_level: str
    support_score: float
    matched_sources: List[Dict[str, Any]]


class ClaimExtractor:
    def extract(self, answer: str, *, max_claims: int = 5) -> List[str]:
        text = str(answer or "").strip()
        if not text:
            return []
        claims: List[str] = []
        for raw in re.split(r"[\n\r]+", text):
            line = raw.strip().lstrip("-").strip()
            if not line:
                continue
            if re.match(r"(?i)^(confidence|sources|follow-ups|what to treat carefully)\b", line):
                continue
            for sentence in re.split(r"(?<=[.!?])\s+", line):
                sent = sentence.strip()
                if len(sent.split()) < 5:
                    continue
                claims.append(sent)
                if len(claims) >= max_claims:
                    return claims
        return claims[:max_claims]


class EvidenceMatrix:
    def build(self, *, answer: str, source_rows: List[Dict[str, Any]], max_claims: int = 5) -> Dict[str, Any]:
        claims = ClaimExtractor().extract(answer, max_claims=max_claims)
        rows: List[EvidenceRow] = []
        for claim in claims:
            scored_matches: List[Dict[str, Any]] = []
            claim_tokens = self._tokens(claim)
            for source in list(source_rows or [])[:12]:
                support_score = self._support_score(claim_tokens, source)
                if support_score <= 0:
                    continue
                scored_matches.append(
                    {
                        "title": str(source.get("title") or source.get("provider") or "Source").strip(),
                        "url": str(source.get("url") or source.get("link") or "").strip() or None,
                        "provider": str(source.get("provider") or "").strip() or None,
                        "support_score": round(support_score, 3),
                    }
                )
            scored_matches.sort(key=lambda item: item["support_score"], reverse=True)
            best_score = float(scored_matches[0]["support_score"]) if scored_matches else 0.0
            if best_score >= 0.58:
                level = "supported"
            elif best_score >= 0.33:
                level = "partially_supported"
            else:
                level = "unsupported"
            rows.append(
                EvidenceRow(
                    claim=claim,
                    support_level=level,
                    support_score=best_score,
                    matched_sources=scored_matches[:3],
                )
            )

        supported = sum(1 for row in rows if row.support_level == "supported")
        partial = sum(1 for row in rows if row.support_level == "partially_supported")
        unsupported = sum(1 for row in rows if row.support_level == "unsupported")
        total = len(rows)
        coverage = 0.0 if total == 0 else (supported + (partial * 0.5)) / total
        return {
            "claims": [
                {
                    "claim": row.claim,
                    "support": row.support_level,
                    "support_level": row.support_level,
                    "support_score": round(row.support_score, 3),
                    "citation_required": True,
                    "sources": [
                        source.get("title") or source.get("url") or "Source"
                        for source in row.matched_sources
                    ],
                    "matched_sources": row.matched_sources,
                }
                for row in rows
            ],
            "summary": {
                "claim_count": total,
                "supported_claims": supported,
                "partially_supported_claims": partial,
                "unsupported_claims": unsupported,
                "citation_coverage": round(coverage, 3),
            },
        }

    def soften_unsupported_claims(self, *, answer: str, source_rows: List[Dict[str, Any]], max_claims: int = 5) -> str:
        report = self.build(answer=answer, source_rows=source_rows, max_claims=max_claims)
        unsupported_claims = {
            str(row.get("claim") or "").strip()
            for row in list(report.get("claims") or [])
            if str(row.get("support_level") or "") == "unsupported"
        }
        if not unsupported_claims:
            return str(answer or "").strip()
        softened: List[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", str(answer or "").strip()):
            stripped = str(sentence or "").strip()
            if not stripped:
                continue
            if stripped in unsupported_claims:
                softened.append(f"Available evidence does not fully verify this claim: {stripped}")
            else:
                softened.append(stripped)
        return " ".join(softened).strip()

    def _support_score(self, claim_tokens: set[str], source: Dict[str, Any]) -> float:
        source_text = " ".join(
            str(source.get(key) or "")
            for key in ("title", "snippet", "summary", "provider", "domain", "published_at", "date_hint")
        )
        source_tokens = self._tokens(source_text)
        if not claim_tokens or not source_tokens:
            return 0.0
        overlap = len(claim_tokens & source_tokens)
        coverage = overlap / max(1, len(claim_tokens))
        if str(source.get("tier") or "").strip().lower() == "official":
            coverage += 0.08
        if str(source.get("published_at") or source.get("date_hint") or "").strip():
            coverage += 0.04
        return min(1.0, coverage)

    def _tokens(self, text: str) -> set[str]:
        words = {
            token
            for token in re.findall(r"[a-z0-9]{3,}", str(text or "").lower())
            if token not in _STOPWORDS
        }
        return words
