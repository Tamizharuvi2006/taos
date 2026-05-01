from __future__ import annotations

import re
from typing import Any, Dict, Iterable


HIGH_STAKES_RE = re.compile(
    r"\b(medical|health|doctor|diagnosis|treatment|medicine|dosage|legal|law|court|"
    r"lawyer|financial|finance|investment|stock|tax|immigration|visa|regulatory|"
    r"regulation|compliance|safety-critical|engineering safety)\b",
    re.I,
)


class HighStakesResearchGuard:
    """Official-source and safe-wording policy for high-stakes research."""

    def evaluate_query(self, query: str) -> Dict[str, Any]:
        text = str(query or "")
        category = self._category(text)
        high = bool(category)
        return {
            "high_stakes": high,
            "category": category,
            "requires_official_source": high,
            "recommended_route": "official_search" if high else None,
        }

    def evaluate_evidence(self, *, query: str, rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        query_summary = self.evaluate_query(query)
        evidence_rows = [dict(row) for row in rows or []]
        official_count = sum(1 for row in evidence_rows if self._is_official(row))
        weak = bool(query_summary["high_stakes"] and official_count <= 0)
        return {
            **query_summary,
            "official_source_count": official_count,
            "official_source_found": official_count > 0,
            "confidence_cap": 0.52 if weak else 0.82 if query_summary["high_stakes"] else None,
            "warning": (
                "High-stakes topic: no official or primary source was verified."
                if weak
                else "High-stakes topic: verify with an official or qualified professional source."
                if query_summary["high_stakes"]
                else None
            ),
        }

    def apply_safe_wording(self, *, answer: str, summary: Dict[str, Any]) -> str:
        output = str(answer or "").rstrip()
        if not output or not bool((summary or {}).get("high_stakes")):
            return output
        lower = output.lower()
        additions = []
        if "not professional advice" not in lower:
            additions.append("This is informational only and is not professional advice.")
        if "official" not in lower and "professional" not in lower:
            additions.append("Verify with an official source or qualified professional before relying on it.")
        if "irreversible action" not in lower:
            additions.append("Do not take irreversible action solely from this answer.")
        if not additions:
            return output
        return output + "\n\nHigh-stakes caution\n" + "\n".join(f"- {line}" for line in additions)

    def _category(self, text: str) -> str | None:
        lower = text.lower()
        categories = {
            "medical": ("medical", "health", "doctor", "diagnosis", "treatment", "medicine", "dosage"),
            "legal": ("legal", "law", "court", "lawyer", "sue", "liability"),
            "financial": ("financial", "finance", "investment", "stock", "securities"),
            "tax": ("tax", "irs", "income tax"),
            "immigration": ("immigration", "visa", "green card"),
            "regulatory": ("regulatory", "regulation", "compliance", "policy"),
            "safety_engineering": ("safety-critical", "engineering safety"),
        }
        for category, terms in categories.items():
            if any(term in lower for term in terms):
                return category
        return None

    def _is_official(self, row: Dict[str, Any]) -> bool:
        tier = str(row.get("tier") or row.get("source_type") or "").lower()
        provider = str(row.get("provider") or row.get("domain") or row.get("link") or row.get("url") or "").lower()
        return tier == "official" or any(token in provider for token in (".gov", ".edu", "sec.gov", "who.int", "fda.gov", "irs.gov", "official"))
