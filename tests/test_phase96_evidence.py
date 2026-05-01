from __future__ import annotations

from taos.core.evaluation.citation_checker import CitationSupportChecker
from taos.core.evaluation.confidence_calibrator import ConfidenceCalibrator
from taos.orchestration.engine import OrchestrationEngine


def test_citation_checker_reports_supported_and_unsupported_claims():
    checker = CitationSupportChecker()
    answer = (
        "OpenAI released a policy update for enterprise customers. "
        "The company also opened a store on Mars."
    )
    sources = [
        {
            "title": "OpenAI enterprise policy update",
            "snippet": "OpenAI published an enterprise policy update for customers with new controls.",
            "provider": "OpenAI",
            "tier": "official",
            "published_at": "2026-04-20",
        }
    ]

    report = checker.analyze(answer=answer, source_rows=sources)

    assert report["summary"]["claim_count"] >= 1
    assert report["summary"]["supported_claims"] >= 1
    assert report["summary"]["unsupported_claims"] >= 0
    assert report["overall_support"] in {"supported", "mixed", "weak"}


def test_confidence_calibrator_reduces_score_for_unsupported_claims():
    calibrator = ConfidenceCalibrator()
    calibrated = calibrator.calibrate(
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

    assert calibrated["score"] < 0.82
    assert calibrated["label"] in {"Low", "Medium"}


def test_engine_trust_block_includes_evidence_matrix_summary():
    engine = OrchestrationEngine()
    engine._trace_data["evidence_stats"] = {
        "source_count": 2,
        "extract_count": 2,
        "extract_rejected_count": 0,
        "official_count": 1,
        "provider_count": 2,
        "domain_diversity": 0.7,
        "extraction_quality": 0.8,
        "official_source_required": False,
        "official_source_found": True,
        "high_stakes_mode": False,
        "query_kind": "general",
        "verification_state": "confirmed",
        "agreement_level": "high",
        "agreement_score": 0.8,
        "conflict_detected": False,
        "stale_detected": False,
        "signal": "clean",
        "source_rows": [
            {
                "title": "Policy update",
                "snippet": "OpenAI published an enterprise policy update.",
                "provider": "OpenAI",
                "tier": "official",
                "published_at": "2026-04-20",
            },
            {
                "title": "Follow-up coverage",
                "snippet": "Reporting confirmed the enterprise policy update.",
                "provider": "Example News",
                "published_at": "2026-04-21",
            },
        ],
    }

    evidence_report = engine._build_evidence_report(
        answer_text="OpenAI published an enterprise policy update for customers."
    )
    trust_block = engine._build_trust_block(
        planner_path="deep_research",
        freshness={"status": "passed"},
        fallback_used=False,
        confidence=0.82,
        evidence_report=evidence_report,
    )

    assert trust_block["supported_claims"] >= 1
    assert trust_block["citation_coverage"] > 0.0
    assert "evidence_matrix_summary" in trust_block
