from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


class ClaimVerifier:
    def verify(
        self,
        *,
        query: str,
        evidence_rows: Iterable[Dict[str, Any]],
    ) -> Dict[str, Any]:
        rows = [dict(row or {}) for row in evidence_rows or []]
        text = " ".join(
            str(row.get(key) or "")
            for row in rows
            for key in ("title", "snippet", "summary", "raw_snippet")
        ).lower()
        if not rows:
            status = "no_usable_evidence"
        elif _supports_exact_claim(query=query, text=text):
            status = "confirmed"
        elif _contradicts_exact_claim(query=query, text=text):
            status = "contradicted"
        elif text:
            status = "related_evidence_only"
        else:
            status = "not_confirmed"
        return {
            "claim_verification_status": status,
            "exact_claim_supported": status == "confirmed",
            "related_evidence_found": status == "related_evidence_only",
            "rows_considered": len(rows),
        }


def _supports_exact_claim(*, query: str, text: str) -> bool:
    q = str(query or "").lower()
    if "block" in q and _mentions_claude_like(q):
        return bool(re.search(r"\b(block|ban|restrict).{0,40}\bclaude\b", text))
    return False


def _contradicts_exact_claim(*, query: str, text: str) -> bool:
    q = str(query or "").lower()
    if "block" in q and _mentions_claude_like(q):
        return all(token in text for token in ("claude", "india")) and any(
            phrase in text for phrase in ("available", "still available", "remains available", "supported countries")
        )
    return False


def _mentions_claude_like(text: str) -> bool:
    return any(token in str(text or "").lower() for token in ("claude", "claudde", "claud", "cluade"))
