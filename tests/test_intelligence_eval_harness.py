from __future__ import annotations

from pathlib import Path

from taos.core.evaluation.intelligence_eval import (
    IntelligenceEvalCase,
    build_tuning_recommendations,
    extract_route_telemetry,
    load_intelligence_eval_cases,
    score_intelligence_response,
    summarize_experimental_metric_averages,
    summarize_metric_averages,
    summarize_route_telemetry,
)


def test_intelligence_eval_fixture_loads_minimum_cases():
    cases = load_intelligence_eval_cases(Path("tests/fixtures/intelligence_eval_cases.json"))
    assert len(cases) >= 6
    ids = {c.case_id for c in cases}
    assert "news_conflict_1" in ids
    assert "doc_exam_1" in ids


def test_intelligence_eval_scores_structured_research_higher():
    cases = load_intelligence_eval_cases(Path("tests/fixtures/intelligence_eval_cases.json"))
    case = next(c for c in cases if c.case_id == "official_required_1")
    response = {
        "answer": (
            "Answer\n"
            "The latest RBI policy statement indicates a hold pattern with tighter commentary [S1].\n\n"
            "Evidence\n"
            "- Official release confirms the policy stance [S1]\n"
            "- Follow-up reporting aligns with the announcement [S2]\n\n"
            "What Is Not Confirmed Yet\n"
            "- Timing details for next revision cycle remain open.\n\n"
            "Bottom line\n"
            "Direction is clear, but execution timing still needs confirmation.\n\n"
            "Next useful follow-up\n"
            "- Want only official statements?\n"
            "- Want a short timeline?"
        ),
        "confidence": 0.79,
        "sources": [
            {"link": "https://www.rbi.org.in/example"},
            {"link": "https://www.reuters.com/world/india/example"},
            {"link": "https://www.bloomberg.com/example"},
        ],
        "trace": {
            "route_label": "deep_research",
            "trust_block": {
                "signal": "clean",
                "source_count": 3,
                "official_source_found": True,
            },
        },
    }
    scored = score_intelligence_response(case, response)
    assert scored["overall_score"] >= 0.7
    assert scored["scores"]["citation_quality"] >= 0.55
    assert scored["scores"]["mode_consistency"] >= 0.7


def test_intelligence_eval_scores_weak_overclaim_lower():
    cases = load_intelligence_eval_cases(Path("tests/fixtures/intelligence_eval_cases.json"))
    case = next(c for c in cases if c.case_id == "weak_evidence_1")
    response = {
        "answer": "It is confirmed and final. No uncertainty. Definitely true.",
        "confidence": 0.94,
        "sources": [],
        "trace": {
            "route_label": "deep_research",
            "trust_block": {
                "signal": "conflicting",
                "source_count": 0,
                "official_source_found": False,
            }
        },
    }
    scored = score_intelligence_response(case, response)
    assert scored["overall_score"] <= 0.6
    assert scored["scores"]["uncertainty_honesty"] <= 0.5
    assert scored["scores"]["confidence_calibration"] <= 0.65


def test_intelligence_eval_recommendations_and_averages():
    low_result = {
        "scores": {
            "clarity": 0.5,
            "correctness": 0.5,
            "grounding_trust": 0.4,
            "uncertainty_honesty": 0.35,
            "citation_quality": 0.25,
            "confidence_calibration": 0.3,
            "followup_usefulness": 0.3,
            "mode_consistency": 0.45,
            "error_case_intelligence": 0.4,
        }
    }
    results = [low_result, low_result]
    averages = summarize_metric_averages(results)
    assert averages["confidence_calibration"] <= 0.35
    recs = build_tuning_recommendations(results)
    assert any("confidence mapping weights" in rec for rec in recs)
    assert any("uncertainty policy" in rec for rec in recs)


def test_intelligence_eval_observed_mode_prefers_planner_path_and_intent():
    cases = load_intelligence_eval_cases(Path("tests/fixtures/intelligence_eval_cases.json"))
    case = next(c for c in cases if c.case_id == "news_conflict_1")
    response = {
        "answer": "Update remains uncertain with conflicting reports.",
        "intent": "news",
        "mode": "fast",
        "trace": {
            "planner_path": "deep_research",
            "mode": "fast",
            "trust_block": {"signal": "partial_conflict", "source_count": 2},
        },
        "sources": [{"link": "https://example.com/a"}, {"link": "https://example.com/b"}],
    }
    scored = score_intelligence_response(case, response)
    assert scored["observed_type"] == "deep_research"


def test_intelligence_eval_v2_fixture_supports_context_and_follow_up():
    cases = load_intelligence_eval_cases(Path("tests/fixtures/intelligence_eval_cases_v2.json"))
    assert len(cases) >= 8
    case = next(c for c in cases if c.case_id == "multi_turn_1")
    assert case.follow_up == "who leaked it?"
    assert case.context == "Jana Nayagan leak details"


def test_intelligence_eval_emits_experimental_scores():
    cases = load_intelligence_eval_cases(Path("tests/fixtures/intelligence_eval_cases_v2.json"))
    case = next(c for c in cases if c.case_id == "adversarial_1")
    response = {
        "answer": (
            "I cannot mark it as confirmed without evidence. "
            "It is not confirmed yet and needs official verification."
        ),
        "trace": {"route_label": "deep_research", "trust_block": {"signal": "conflicting"}},
        "sources": [],
    }
    scored = score_intelligence_response(case, response)
    exp = scored["experimental_scores"]
    assert "context_usage" in exp
    assert "multi_intent_handling" in exp
    assert "safety_integrity" in exp
    assert exp["refusal_integrity"] >= 0.8


def test_intelligence_eval_emits_route_telemetry_summary():
    cases = load_intelligence_eval_cases(Path("tests/fixtures/intelligence_eval_cases_v2.json"))
    case = next(c for c in cases if c.case_id == "high_stakes_weak_1")
    response = {
        "answer": "Not confirmed yet. Official sources should be checked before acting.",
        "trace": {
            "route_label": "deep_research",
            "planner_path": "deep_research",
            "route_source": "phase107_rules",
            "route_confidence": 0.9,
            "route_boundary_summary": {
                "owner": "research_pipeline",
                "boundary": "deterministic",
                "used_llm": False,
            },
            "trust_block": {"signal": "conflicting", "source_count": 1},
        },
        "sources": [{"link": "https://rbi.org.in/example"}],
    }
    scored = score_intelligence_response(case, response)
    telemetry = scored["route_telemetry"]
    assert telemetry["matched_expected"] is True
    assert telemetry["owner"] == "research_pipeline"
    summary = summarize_route_telemetry([scored])
    assert summary["route_match_rate"] == 1.0
    assert summary["route_mismatch_count"] == 0


def test_intelligence_eval_experimental_metric_averages():
    rows = [
        {
            "experimental_scores": {
                "context_usage": 1.0,
                "intent_handling": 0.8,
                "multi_intent_handling": 0.9,
                "hallucination_resistance": 0.7,
                "refusal_integrity": 0.9,
                "safety_integrity": 0.85,
            }
        },
        {
            "experimental_scores": {
                "context_usage": 0.5,
                "intent_handling": 0.6,
                "multi_intent_handling": 0.4,
                "hallucination_resistance": 0.8,
                "refusal_integrity": 0.95,
                "safety_integrity": 0.9,
            }
        },
    ]
    summary = summarize_experimental_metric_averages(rows)
    assert summary["context_usage"] == 0.75
    assert summary["safety_integrity"] == 0.875


def test_intelligence_eval_route_alias_accepts_specific_research_route_for_deep_research_expectation():
    case = IntelligenceEvalCase(
        case_id="alias_ok",
        query="latest official update",
        expected_type="deep_research",
        checks=[],
    )
    telemetry = extract_route_telemetry(
        case=case,
        response={"trace": {"route_label": "news_search"}},
        observed_mode="news_search",
    )
    assert telemetry["matched_expected"] is True
    assert telemetry["observed_type"] == "news_search"


def test_intelligence_eval_route_telemetry_keeps_specific_expected_routes_strict():
    case = IntelligenceEvalCase(
        case_id="alias_strict",
        query="latest official update",
        expected_type="news_search",
        checks=[],
    )
    telemetry = extract_route_telemetry(
        case=case,
        response={"trace": {"route_label": "deep_research"}},
        observed_mode="deep_research",
    )
    assert telemetry["matched_expected"] is False


def test_intelligence_eval_route_telemetry_uses_route_owner_fallback_from_route_decision():
    case = IntelligenceEvalCase(
        case_id="owner_fallback",
        query="what is docker",
        expected_type="standard_task",
        checks=[],
    )
    telemetry = extract_route_telemetry(
        case=case,
        response={
            "metadata": {
                "route_decision": {
                    "route_owner": "direct_llm_no_tools",
                    "used_llm": False,
                    "boundary": "deterministic",
                }
            }
        },
        observed_mode="no_search",
    )
    assert telemetry["owner"] == "direct_llm_no_tools"
