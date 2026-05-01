from __future__ import annotations

from typing import Any, Dict, Iterable, List


class RelatedEvidenceFinder:
    def split(self, *, claim_terms: Iterable[str], rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        terms = [str(term or "").strip().lower() for term in claim_terms if str(term or "").strip()]
        confirming: List[Dict[str, Any]] = []
        contradicting: List[Dict[str, Any]] = []
        related: List[Dict[str, Any]] = []
        for raw in rows or []:
            row = dict(raw or {})
            text = " ".join(
                str(row.get(key) or "")
                for key in ("title", "snippet", "summary", "raw_snippet", "link", "url")
            ).lower()
            has_terms = all(term in text for term in terms) if terms else False
            if any(token in text for token in ("supported countries", "supported regions", "available in india", "still available", "officially available")):
                contradicting.append(row)
                continue
            if has_terms and any(token in text for token in ("blocked", "ban", "banned", "restrict", "restriction")):
                confirming.append(row)
                continue
            related.append(row)
        return {
            "confirming": confirming[:3],
            "contradicting": contradicting[:3],
            "related": related[:3],
        }

    def find(self, *, query: str, rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        evidence_rows = [dict(row or {}) for row in rows or []]
        related = []
        source_of_record = []
        contradiction = []
        for row in evidence_rows:
            text = " ".join(
                str(row.get(key) or "")
                for key in ("title", "snippet", "summary", "raw_snippet", "link", "url")
            ).lower()
            if any(token in text for token in ("supported countries", "supported regions", "available in india", "still available")):
                source_of_record.append(row)
                contradiction.append(row)
            if any(token in text for token in ("mythos", "cybersecurity", "risk", "rbi", "banks", "regulator")):
                related.append(row)
            elif row not in source_of_record:
                related.append(row)
        best = source_of_record[0] if source_of_record else (related[0] if related else None)
        return {
            "best_available_finding": _best_finding(best),
            "related_rows": related[:3],
            "source_of_record_rows": source_of_record[:2],
            "contradiction_rows": contradiction[:2],
            "related_evidence_used": bool(related or source_of_record),
            "related_evidence_sources_count": len(related[:3]) + len(source_of_record[:2]),
            "source_of_record_checked": bool(source_of_record),
        }


def _best_finding(row: Dict[str, Any] | None) -> str:
    if not row:
        return ""
    return str(row.get("snippet") or row.get("summary") or row.get("title") or "").strip()[:260]
