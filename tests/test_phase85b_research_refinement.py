from __future__ import annotations

from datetime import datetime, timezone

from taos.core.semantic.intent_classifier import DomainType
from taos.core.tools.source_ranker import SourceRanker
from taos.orchestration.engine import OrchestrationEngine


def test_source_ranker_freshness_weighting_prioritizes_recent_news():
    ranker = SourceRanker()
    rows = [
        {
            "title": "Older update",
            "link": "https://news-a.com/story-1",
            "snippet": "Status update from early March 2026.",
            "published_at": "2026-03-01",
        },
        {
            "title": "Latest update",
            "link": "https://news-b.com/story-2",
            "snippet": "Status update from April 2026 with current developments.",
            "published_at": "2026-04-09",
        },
    ]
    ranked = ranker.rank(
        rows,
        domain=DomainType.GENERAL,
        max_results=5,
        freshness_sensitive=True,
        reference_time=datetime(2026, 4, 10, tzinfo=timezone.utc),
    )
    assert ranked
    assert ranked[0].title == "Latest update"
    assert ranked[0].rank_score > ranked[1].rank_score
    assert ranked[0].freshness_score >= ranked[1].freshness_score


def test_source_ranker_enforces_domain_cap():
    ranker = SourceRanker()
    rows = [
        {"title": "A1", "link": "https://same-domain.com/a1", "snippet": "x"},
        {"title": "A2", "link": "https://same-domain.com/a2", "snippet": "x"},
        {"title": "A3", "link": "https://same-domain.com/a3", "snippet": "x"},
        {"title": "B1", "link": "https://other-domain.com/b1", "snippet": "x"},
    ]
    ranked = ranker.rank(rows, max_results=10, max_per_provider=1)
    providers = [r.provider for r in ranked]
    assert providers.count("same-domain.com") <= 1


def test_phase85b_query_budget_rules():
    engine = OrchestrationEngine()
    assert engine._determine_research_query_budget(goal="current conflict latest update", freshness_mode=True) == 4
    assert engine._determine_research_query_budget(goal="explain tcp", freshness_mode=False) == 2
    assert engine._determine_research_query_budget(goal="comprehensive compare two models with analysis", freshness_mode=False) == 4
    assert engine._determine_research_query_budget(goal="summarize this topic with recent updates and context", freshness_mode=False) == 3


def test_phase85b_agreement_scoring_flags_conflict_and_stale():
    engine = OrchestrationEngine()
    rows = [
        {
            "title": "Source one says escalation continues",
            "snippet": "Reports conflict and contradicting timelines.",
            "provider": "alpha.com",
            "tier": "trusted",
            "date_hint": "2026-03-01",
        },
        {
            "title": "Source two says de-escalation happened",
            "snippet": "However, dispute remains unresolved.",
            "provider": "beta.com",
            "tier": "trusted",
            "date_hint": "2026-03-02",
        },
    ]
    agreement = engine._compute_research_agreement(rows, freshness_mode=True)
    assert agreement["conflict_detected"] is True
    assert agreement["stale_detected"] is True
    assert agreement["agreement_level"] in {"low", "medium"}


def test_phase85b_agreement_distinguishes_event_vs_attribution_uncertainty():
    engine = OrchestrationEngine()
    rows = [
        {
            "title": "Film leak confirmed online",
            "snippet": "The HD leak is confirmed but who leaked it remains unclear.",
            "provider": "alpha.com",
            "tier": "trusted",
            "date_hint": "2026-04-09",
        },
        {
            "title": "Legal action after leak",
            "snippet": "Multiple reports confirm the leak event; attribution is not confirmed.",
            "provider": "beta.com",
            "tier": "trusted",
            "date_hint": "2026-04-09",
        },
    ]
    agreement = engine._compute_research_agreement(
        rows,
        freshness_mode=False,
        goal="how did Jana Nayagan leak and who leaked it",
    )
    assert agreement["event_agreement_level"] in {"medium", "high"}
    assert agreement["attribution_agreement_level"] == "low"
    assert agreement["signal"] == "partial_conflict"
    assert agreement["agreement_level"] in {"medium", "high"}


def test_source_ranker_official_source_required_adjusts_scores():
    ranker = SourceRanker()
    non_official_rows = [
        {
            "title": "Policy commentary",
            "link": "https://analysis-site.com/post",
            "snippet": "Detailed commentary about policy updates and implications for markets.",
        }
    ]
    no_req = ranker.rank(non_official_rows, domain=DomainType.GENERAL, official_source_required=False)
    req = ranker.rank(non_official_rows, domain=DomainType.GENERAL, official_source_required=True)
    assert no_req and req
    assert req[0].rank_score < no_req[0].rank_score

    official_rows = [
        {
            "title": "Official policy statement",
            "link": "https://www.sec.gov/news/press-release/example",
            "snippet": "Official release with finalized policy language.",
        }
    ]
    no_req_official = ranker.rank(official_rows, domain=DomainType.GENERAL, official_source_required=False)
    req_official = ranker.rank(official_rows, domain=DomainType.GENERAL, official_source_required=True)
    assert no_req_official and req_official
    assert req_official[0].tier == "official"
    assert req_official[0].rank_score > no_req_official[0].rank_score


def test_phase85b_official_source_required_marks_missing_official_as_partial_conflict():
    engine = OrchestrationEngine()
    rows = [
        {
            "title": "Market blog interpretation",
            "snippet": "Analysts expect a rate hold based on rumors.",
            "provider": "analysis-site.com",
            "tier": "other",
            "date_hint": "2026-04-09",
        },
        {
            "title": "Newswire expectation note",
            "snippet": "No official statement published yet.",
            "provider": "news-site.com",
            "tier": "trusted",
            "date_hint": "2026-04-09",
        },
    ]
    agreement = engine._compute_research_agreement(
        rows,
        freshness_mode=False,
        goal="What did RBI officially announce in latest policy statement?",
    )
    assert agreement["official_source_required"] is True
    assert agreement["official_source_found"] is False
    assert agreement["signal"] in {"partial_conflict", "conflicting"}


def test_phase85b_extract_prefilter_skips_obvious_junk_urls():
    engine = OrchestrationEngine()
    rows = [
        {"title": "Tag index", "link": "https://news.example.com/tag/war", "snippet": "index"},
        {"title": "Search page", "link": "https://news.example.com/search?q=war", "snippet": "search results"},
        {"title": "Login wall", "link": "https://news.example.com/login", "snippet": "signin required"},
        {"title": "Good report", "link": "https://news.example.com/world/report-123", "snippet": "Detailed conflict report with updates and context."},
    ]
    pre = engine._prefilter_extract_candidates(rows, max_keep=3)
    kept = pre["kept"]
    assert len(kept) == 1
    assert kept[0]["link"].endswith("/world/report-123")
    assert pre["skipped"] >= 3
    reasons = pre["reasons"]
    assert reasons.get("index_like_url", 0) >= 1
    assert reasons.get("gated_url", 0) >= 1


def test_phase85b_high_stakes_query_forces_official_requirement():
    engine = OrchestrationEngine()
    rows = [
        {
            "title": "Analyst take on drug dosage update",
            "snippet": "Commentary article without primary medical authority publication.",
            "provider": "analysis-site.com",
            "tier": "other",
            "date_hint": "2026-04-09",
        },
        {
            "title": "Blog summary",
            "snippet": "Second-hand interpretation of treatment guidance.",
            "provider": "blog-site.com",
            "tier": "other",
            "date_hint": "2026-04-09",
        },
    ]
    agreement = engine._compute_research_agreement(
        rows,
        freshness_mode=False,
        goal="What is the latest official dosage guidance for this medicine?",
    )
    assert agreement["high_stakes_mode"] is True
    assert agreement["official_source_required"] is True
    assert agreement["official_source_found"] is False
    assert agreement["signal"] in {"partial_conflict", "conflicting"}


def test_phase85b_official_source_injected_into_ranked_rows_when_required():
    engine = OrchestrationEngine()
    rows = [
        {"title": "Newswire A", "link": "https://news-a.com/a", "tier": "trusted", "provider": "news-a.com"},
        {"title": "Newswire B", "link": "https://news-b.com/b", "tier": "trusted", "provider": "news-b.com"},
    ]
    ranked_pool = rows + [
        {
            "title": "Official release",
            "link": "https://www.sec.gov/news/press-release/sample",
            "tier": "official",
            "provider": "sec.gov",
        }
    ]
    out = engine._ensure_official_source_in_ranked_rows(rows, ranked_pool, required=True, max_keep=3)
    assert out
    assert str(out[0].get("tier")) == "official"


def test_phase85b_official_source_injected_into_extract_candidates_when_required():
    engine = OrchestrationEngine()
    candidates = [
        {"title": "Newswire A", "link": "https://news-a.com/a", "tier": "trusted", "provider": "news-a.com"},
        {"title": "Newswire B", "link": "https://news-b.com/b", "tier": "trusted", "provider": "news-b.com"},
    ]
    ranked_pool = [
        {"title": "Newswire A", "link": "https://news-a.com/a", "tier": "trusted", "provider": "news-a.com"},
        {
            "title": "Official update",
            "link": "https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=12345",
            "tier": "official",
            "provider": "rbi.org.in",
        },
    ]
    out = engine._ensure_official_source_in_extract_candidates(
        candidates,
        ranked_pool,
        required=True,
        max_keep=2,
    )
    assert out
    assert any(str(row.get("tier")) == "official" for row in out)


def test_phase85b_high_stakes_unverified_message_mentions_official_sources():
    engine = OrchestrationEngine()
    out = engine._build_research_unverified_message(
        "latest legal compliance update",
        high_stakes_mode=True,
        official_source_required=True,
    )
    lowered = out.lower()
    assert "couldn't verify this confidently" in lowered
    assert "official-source evidence" in lowered
    assert "next useful moves" in lowered


def test_phase85b_trust_block_high_stakes_downgrades_confidence_and_sets_guard_flag():
    engine = OrchestrationEngine()
    engine._trace_data = {
        "evidence_stats": {
            "source_count": 3,
            "provider_count": 3,
            "official_count": 0,
            "trusted_count": 2,
            "extract_count": 2,
            "extract_fetch_count": 2,
            "extract_rejected_count": 0,
            "extraction_quality": 0.78,
            "domain_diversity": 0.9,
            "last_verified": "2026-04-09",
            "official_source_required": True,
            "official_source_found": False,
            "agreement_level": "medium",
            "agreement_score": 0.62,
            "conflict_detected": False,
            "stale_detected": False,
            "signal": "clean",
            "event_agreement_level": "medium",
            "attribution_agreement_level": "not_applicable",
            "high_stakes_mode": True,
        }
    }
    trust = engine._build_trust_block(
        planner_path="deep_research",
        freshness={"status": "passed"},
        fallback_used=False,
        confidence=0.95,
    )
    assert trust["confidence"] == "Low"
    assert trust["high_stakes_mode"] is True
    assert "official_source_missing" in trust["uncertainty_flags"]
    assert "high_stakes_guard" in trust["uncertainty_flags"]


def test_phase91_confidence_calibration_tracks_evidence_strength():
    engine = OrchestrationEngine()
    engine._trace_data = {
        "evidence_stats": {
            "source_count": 5,
            "provider_count": 4,
            "official_count": 1,
            "trusted_count": 4,
            "extract_count": 3,
            "extract_fetch_count": 3,
            "extract_rejected_count": 0,
            "extraction_quality": 0.84,
            "domain_diversity": 0.82,
            "last_verified": "2026-04-09",
            "official_source_required": True,
            "official_source_found": True,
            "agreement_level": "high",
            "agreement_score": 0.86,
            "conflict_detected": False,
            "stale_detected": False,
            "signal": "clean",
            "event_agreement_level": "high",
            "attribution_agreement_level": "not_applicable",
            "high_stakes_mode": False,
        }
    }
    strong = engine._build_trust_block(
        planner_path="deep_research",
        freshness={"status": "passed"},
        fallback_used=False,
        confidence=0.55,
    )
    assert strong["confidence"] == "High"
    assert float(strong.get("confidence_score") or 0.0) >= 0.78

    engine._trace_data["evidence_stats"].update(
        {
            "source_count": 1,
            "provider_count": 1,
            "official_count": 0,
            "extract_count": 0,
            "extract_fetch_count": 0,
            "extract_rejected_count": 1,
            "extraction_quality": 0.21,
            "domain_diversity": 0.08,
            "official_source_required": True,
            "official_source_found": False,
            "agreement_level": "low",
            "agreement_score": 0.22,
            "conflict_detected": True,
            "stale_detected": True,
            "signal": "conflicting",
            "high_stakes_mode": True,
        }
    )
    weak = engine._build_trust_block(
        planner_path="deep_research",
        freshness={"status": "failed"},
        fallback_used=True,
        confidence=0.92,
    )
    assert weak["confidence"] == "Low"
    assert float(weak.get("confidence_score") or 1.0) < 0.52
