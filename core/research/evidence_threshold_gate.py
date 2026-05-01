from __future__ import annotations

from typing import Any, Dict


class EvidenceThresholdGate:
    def evaluate(
        self,
        *,
        usable_sources_count: int,
        selected_rows: int,
        coverage: float,
        claim_verification_status: str = "",
    ) -> Dict[str, Any]:
        status = str(claim_verification_status or "").strip().lower()
        passed = bool(usable_sources_count > 0 and selected_rows > 0 and float(coverage or 0.0) > 0.0)
        if not passed:
            if status == "related_evidence_only":
                answer_mode = "related_evidence_only"
            else:
                answer_mode = "no_usable_evidence"
        else:
            answer_mode = "best_supported"
        return {
            "evidence_threshold_passed": passed,
            "answer_mode": answer_mode,
            "usable_sources_count": int(usable_sources_count or 0),
            "selected_rows": int(selected_rows or 0),
            "coverage": float(coverage or 0.0),
        }
