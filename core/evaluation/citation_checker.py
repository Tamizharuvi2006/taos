"""
Citation support checker built on the evidence matrix.
"""

from __future__ import annotations

from typing import Any, Dict, List

from taos.core.research.evidence_matrix import EvidenceMatrix


class CitationSupportChecker:
    def __init__(self) -> None:
        self._matrix = EvidenceMatrix()

    def analyze(self, *, answer: str, source_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        report = self._matrix.build(answer=answer, source_rows=source_rows)
        summary = dict(report.get("summary") or {})
        unsupported = int(summary.get("unsupported_claims") or 0)
        partial = int(summary.get("partially_supported_claims") or 0)
        claim_count = int(summary.get("claim_count") or 0)
        if claim_count <= 0:
            overall = "not_applicable"
        elif unsupported == 0 and partial <= 1:
            overall = "supported"
        elif unsupported <= max(1, claim_count // 3):
            overall = "mixed"
        else:
            overall = "weak"
        return {
            **report,
            "overall_support": overall,
        }

    def soften_unsupported_claims(self, *, answer: str, source_rows: List[Dict[str, Any]]) -> str:
        return self._matrix.soften_unsupported_claims(answer=answer, source_rows=source_rows)
