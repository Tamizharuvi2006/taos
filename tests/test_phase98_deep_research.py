from __future__ import annotations

import pytest

from taos.core.evaluation.confidence_calibrator import ConfidenceCalibrator
from taos.core.research.source_quality import SourceQualityScorer
from taos.orchestration.engine import OrchestrationEngine


def test_unsupported_claims_reduce_confidence():
    calibrator = ConfidenceCalibrator()
    result = calibrator.calibrate(
        base_confidence=0.82,
        citation_report={
            "summary": {
                "claim_count": 3,
                "supported_claims": 1,
                "partially_supported_claims": 0,
                "unsupported_claims": 2,
                "citation_coverage": 0.33,
            }
        },
        stale_detected=False,
        conflict_detected=False,
        high_stakes_mode=False,
    )
    assert result["score"] < 0.6


def test_source_diversity_caps_two_per_domain():
    scorer = SourceQualityScorer()
    rows = [
        scorer.score({"title": "A1", "link": "https://same.com/1", "provider": "same.com", "tier": "official", "freshness_score": 0.8}),
        scorer.score({"title": "A2", "link": "https://same.com/2", "provider": "same.com", "tier": "official", "freshness_score": 0.7}),
        scorer.score({"title": "A3", "link": "https://same.com/3", "provider": "same.com", "tier": "official", "freshness_score": 0.6}),
        scorer.score({"title": "B1", "link": "https://other.com/1", "provider": "other.com", "tier": "trusted", "freshness_score": 0.7}),
    ]
    diversified = scorer.diversify(rows, max_per_domain=2)
    same_domain = [row for row in diversified if row["provider"] == "same.com"]
    assert len(same_domain) == 2


@pytest.mark.asyncio
async def test_deep_research_extract_failure_returns_snippet_backed_answer_with_warning(monkeypatch):
    async def _fake_web_search(*args, **kwargs):
        return {
            "success": True,
            "results": [
                {
                    "title": "Current report",
                    "link": "https://example.com/report",
                    "snippet": "Recent report confirms a current status update.",
                }
            ],
        }

    async def _fake_extract(*args, **kwargs):
        return {"success": False}

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.extract_with_adapter", _fake_extract)

    engine = OrchestrationEngine()
    engine._reset_execution_trace(request_id="req_phase98_extract", goal="latest topic y status", include_trace=True)
    out = await engine._run_deep_research("latest topic y status")

    assert out is not None
    assert "key evidence" in out.lower()
    assert engine._trace_data["fallback_used"] is True
    assert engine._trace_data["evidence_stats"]["extraction_recovery_used"] is True
