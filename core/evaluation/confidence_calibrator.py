"""
Confidence calibration helpers using evidence support signals.
"""

from __future__ import annotations

from typing import Any, Dict


class ConfidenceCalibrator:
    def calibrate(
        self,
        *,
        base_confidence: float,
        citation_report: Dict[str, Any] | None,
        stale_detected: bool,
        conflict_detected: bool,
        high_stakes_mode: bool,
        unresolved_conflict_count: int = 0,
        official_source_missing: bool = False,
    ) -> Dict[str, Any]:
        base = max(0.0, min(1.0, float(base_confidence or 0.0)))
        report = dict(citation_report or {})
        summary = dict(report.get("summary") or {})
        coverage = float(summary.get("citation_coverage") or 0.0)
        unsupported = int(summary.get("unsupported_claims") or 0)
        partial = int(summary.get("partially_supported_claims") or 0)
        claim_count = int(summary.get("claim_count") or 0)

        adjusted = base
        if claim_count > 0:
            adjusted = (base * 0.55) + (coverage * 0.45)
        if unsupported > 0:
            adjusted -= min(0.24, unsupported * 0.08)
        if partial > 1:
            adjusted -= min(0.12, (partial - 1) * 0.04)
        if stale_detected:
            adjusted -= 0.1
        if conflict_detected:
            adjusted -= 0.12
        if unresolved_conflict_count > 0:
            adjusted -= min(0.18, float(unresolved_conflict_count) * 0.06)
        if high_stakes_mode and coverage < 0.6:
            adjusted -= 0.12
        if high_stakes_mode and official_source_missing:
            adjusted -= 0.12
        if claim_count > 0 and coverage < 0.4:
            adjusted -= 0.08
        adjusted = max(0.08, min(0.98, adjusted))

        if adjusted >= 0.78:
            label = "High"
        elif adjusted >= 0.52:
            label = "Medium"
        else:
            label = "Low"
        return {
            "score": round(adjusted, 3),
            "label": label,
            "citation_coverage": round(coverage, 3),
        }
