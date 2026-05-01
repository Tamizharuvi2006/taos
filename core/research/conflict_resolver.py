from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List


class ConflictResolver:
    """Lightweight claim-conflict grouping and confidence signal extraction."""

    def resolve(self, *, evidence_rows: Iterable[Dict[str, Any]], claims: Iterable[str] | None = None) -> Dict[str, Any]:
        rows = [dict(row) for row in evidence_rows or []]
        extracted = [str(claim).strip() for claim in claims or [] if str(claim).strip()]
        if not extracted:
            extracted = self._extract_claims(rows)
        groups = self._group_claims(extracted, rows)
        unresolved = [group for group in groups if group.get("status") in {"direct_conflict", "partial_conflict"} and not group.get("resolved")]
        direct = [group for group in groups if group.get("status") == "direct_conflict"]
        conflict_groups = [
            {
                "claim": str(group.get("topic") or "disputed claim"),
                "sources_for": self._source_refs_for_group(group, rows, positive=True),
                "sources_against": self._source_refs_for_group(group, rows, positive=False),
                "status": group.get("status"),
            }
            for group in groups
            if group.get("status") in {"direct_conflict", "partial_conflict"}
        ]
        conflict_detected = bool(unresolved or direct)
        return {
            "groups": groups,
            "summary": {
                "claim_count": len(extracted),
                "conflict_detected": conflict_detected,
                "agreement_level": "mixed" if conflict_detected else "high",
                "groups": conflict_groups,
                "conflict_group_count": len(direct),
                "unresolved_conflict_count": len(unresolved),
                "requires_uncertainty_wording": bool(unresolved),
                "preferred_side_count": sum(1 for group in groups if group.get("resolved")),
            },
        }

    def _extract_claims(self, rows: List[Dict[str, Any]]) -> List[str]:
        claims: List[str] = []
        for row in rows:
            text = f"{row.get('title', '')}. {row.get('snippet', '')}"
            for sentence in re.split(r"(?<=[.!?])\s+", text):
                sent = sentence.strip()
                if len(sent.split()) >= 5:
                    claims.append(sent[:280])
        return claims[:24]

    def _group_claims(self, claims: List[str], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        buckets: Dict[str, List[str]] = defaultdict(list)
        for claim in claims:
            key = self._topic_key(claim)
            buckets[key].append(claim)
        groups = []
        for topic, topic_claims in buckets.items():
            numbers = sorted({match for claim in topic_claims for match in re.findall(r"\b\d+(?:\.\d+)?%?\b", claim)})
            negated = any(re.search(r"\b(not|no|never|denied|false|unconfirmed)\b", claim, re.I) for claim in topic_claims)
            affirmed = any(re.search(r"\b(is|are|was|were|confirmed|announced|approved|released)\b", claim, re.I) for claim in topic_claims)
            status = "agreement"
            if len(numbers) > 1:
                status = "direct_conflict"
            elif negated and affirmed:
                status = "partial_conflict"
            side_scores = self._score_sides(topic_claims, rows)
            resolved = bool(status != "agreement" and side_scores and side_scores[0]["score"] >= side_scores[-1]["score"] + 0.25)
            groups.append(
                {
                    "topic": topic,
                    "status": status,
                    "claims": topic_claims[:6],
                    "numeric_values": numbers,
                    "side_scores": side_scores[:3],
                    "resolved": resolved,
                    "preferred_claim": side_scores[0]["claim"] if resolved and side_scores else None,
                }
            )
        return groups

    def _score_sides(self, claims: List[str], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        scored = []
        for claim in claims:
            score = 0.4
            for row in rows:
                text = f"{row.get('title', '')} {row.get('snippet', '')}"
                if self._overlap(claim, text) >= 0.25:
                    score += float(row.get("selection_score") or row.get("source_quality") or row.get("score") or 0.45) * 0.4
                    if str(row.get("tier") or "").lower() == "official":
                        score += 0.2
                    if row.get("date_hint") or row.get("published_at"):
                        score += 0.08
            scored.append({"claim": claim, "score": round(min(1.0, score), 3)})
        scored.sort(key=lambda row: row["score"], reverse=True)
        return scored

    def _source_refs_for_group(self, group: Dict[str, Any], rows: List[Dict[str, Any]], *, positive: bool) -> List[str]:
        claims = [str(claim or "") for claim in group.get("claims") or []]
        if not claims:
            return []
        refs: List[str] = []
        marker_re = re.compile(r"\b(not|no|never|denied|false|unconfirmed|dispute|contradict)\b", re.I)
        for idx, row in enumerate(rows, start=1):
            text = f"{row.get('title', '')} {row.get('snippet', '')}"
            neg = bool(marker_re.search(text))
            if neg == positive:
                continue
            if any(self._overlap(claim, text) >= 0.18 for claim in claims):
                refs.append(f"S{idx}")
            if len(refs) >= 4:
                break
        return refs or (["S1"] if rows and positive else [])

    def _topic_key(self, claim: str) -> str:
        tokens = [t for t in re.findall(r"[a-z0-9]{4,}", claim.lower()) if t not in {"this", "that", "with", "from", "have", "were", "will"}]
        return " ".join(tokens[:4]) or "general"

    def _overlap(self, left: str, right: str) -> float:
        a = set(re.findall(r"[a-z0-9]{3,}", left.lower()))
        b = set(re.findall(r"[a-z0-9]{3,}", right.lower()))
        return len(a & b) / max(1, len(a))
