from __future__ import annotations

from pathlib import Path

from taos.core.evaluation.research_eval import load_research_eval_cases, score_research_response


def test_research_eval_fixture_loads_minimum_cases():
    cases = load_research_eval_cases(Path("tests/fixtures/research_eval_cases.json"))
    assert len(cases) >= 6
    ids = {c.case_id for c in cases}
    assert "current_news_query" in ids
    assert "weak_evidence_query" in ids


def test_research_eval_scores_strong_response_higher():
    cases = load_research_eval_cases(Path("tests/fixtures/research_eval_cases.json"))
    case = next(c for c in cases if c.case_id == "current_news_query")
    response = {
        "answer": (
            "Answer\n"
            "Latest status is stable with no confirmed disruption expansion.\n\n"
            "Evidence\n"
            "- Shipping updates remained limited — Reuters [S1]\n"
            "- Advisory still active — IMO [S2]\n\n"
            "Bottom line\n"
            "Situation remains tense but contained."
        ),
        "sources": [
            {"link": "https://www.reuters.com/world/middle-east/example"},
            {"link": "https://www.imo.org/en/example"},
            {"link": "https://www.bbc.com/news/example"},
        ],
        "trace": {
            "trust_block": {
                "freshness": "High",
                "signal": "clean",
                "domain_diversity": 0.66,
                "extraction_quality": 0.82,
            }
        },
    }
    scored = score_research_response(case, response)
    assert scored["overall_score"] >= 0.65
    assert scored["scores"]["citation_usefulness"] >= 0.5
    assert scored["scores"]["source_diversity"] >= 0.6


def test_research_eval_scores_weak_response_lower():
    cases = load_research_eval_cases(Path("tests/fixtures/research_eval_cases.json"))
    case = next(c for c in cases if c.case_id == "weak_evidence_query")
    response = {
        "answer": "It is confirmed and final. No uncertainty.",
        "sources": [],
        "trace": {"trust_block": {"freshness": "Low", "signal": "clean", "domain_diversity": 0.0}},
    }
    scored = score_research_response(case, response)
    assert scored["overall_score"] <= 0.6
    assert scored["scores"]["uncertainty_honesty"] <= 0.5
