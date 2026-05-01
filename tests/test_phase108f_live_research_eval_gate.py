import pytest

from taos.core.evaluation.live_eval_guard import LiveEvalGuard
from taos.core.evaluation.research_eval import ResearchEvalCase, ResearchEvalHarness
from scripts.run_research_eval import MockResearchRunner, _live_report_paths


def _case(**overrides):
    data = {
        "id": "phase108f_latest_openai_models",
        "query": "latest OpenAI API model changes",
        "expected_route": "news_search",
        "category": "phase108f",
        "requires_fresh_sources": True,
        "requires_official_source": True,
    }
    data.update(overrides)
    return ResearchEvalCase.from_dict(data)


@pytest.mark.asyncio
async def test_mock_mode_scores_answer_utility_and_source_quality():
    case = _case()
    evaluation = await ResearchEvalHarness().evaluate_cases(cases=[case], runner=MockResearchRunner())
    result = evaluation["results"][0]

    assert result["route_pass"] is True
    assert result["answer_not_empty"] is True
    assert result["answer_first"] is True
    assert result["answer_mode"] == "best_supported"
    assert result["official_source_count"] >= 1
    assert result["coverage"] >= 0.75
    assert result["unsupported_critical_claims"] == 0
    assert result["passed"] is True
    assert evaluation["aggregates"]["answer_utility"] >= 0.75
    assert evaluation["aggregates"]["source_quality"] >= 0.75


def test_live_mode_requires_explicit_env_readiness(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    guard = LiveEvalGuard(required_env=["SERPER_API_KEY", "OPENROUTER_API_KEY"])

    readiness = guard.readiness(live_requested=True)

    assert readiness["ready"] is False
    assert readiness["mode"] == "live"
    assert set(readiness["missing_env"]) == {"SERPER_API_KEY", "OPENROUTER_API_KEY"}


def test_max_live_cases_is_enforced():
    guard = LiveEvalGuard(max_cases=1, max_cost_estimate=10.0, max_runtime_seconds=60.0)

    assert guard.before_case("one")["allowed"] is True
    denied = guard.before_case("two")

    assert denied["allowed"] is False
    assert denied["reason"] == "max_cases_exceeded"
    assert guard.summary()["cases_run"] == 1


def test_generic_failure_is_penalized_when_usable_evidence_exists():
    case = _case()
    payload = {
        "route": "news_search",
        "answer": "I could not verify this from reliable sources.",
        "confidence": 0.7,
        "sources": ["https://platform.openai.com/docs/models"],
        "metadata": {
            "evidence_stats": {
                "answer_mode": "best_supported",
                "usable_sources_count": 1,
                "official_source_count": 1,
                "extract_attempted_count": 1,
                "extract_success_count": 1,
                "coverage": 0.8,
            }
        },
    }

    result = ResearchEvalHarness()._score_case(case=case, payload=payload, latency_ms=1000)

    assert result.generic_failure_detected is True
    assert result.answer_utility < 0.75
    assert result.fallback_usefulness < 0.75
    assert result.passed is False


def test_weak_evidence_can_pass_when_uncertainty_mode_is_correct():
    case = _case(
        id="phase108f_weak_private_startup",
        query="latest unknown private startup AI model release",
        expected_route="news_search",
        expects_weak_or_no_evidence=True,
        must_not_hallucinate=True,
    )
    payload = {
        "route": "news_search",
        "answer_mode": "weak_candidate",
        "answer": (
            "Answer\n"
            "The best-supported answer is a weak candidate, not a confirmed release. [S1]\n\n"
            "Confidence\n"
            "Low, because only weak evidence was available."
        ),
        "confidence": 0.35,
        "sources": ["https://example.com/private-startup"],
        "metadata": {
            "evidence_stats": {
                "answer_mode": "weak_candidate",
                "usable_sources_count": 1,
                "extract_attempted_count": 1,
                "extract_success_count": 1,
                "coverage": 0.65,
            }
        },
    }

    result = ResearchEvalHarness()._score_case(case=case, payload=payload, latency_ms=1000)

    assert result.answer_mode == "weak_candidate"
    assert result.generic_failure_detected is False
    assert result.fallback_usefulness >= 0.9
    assert result.passed is True


def test_live_report_paths_are_timestamped(tmp_path):
    report_path, json_path = _live_report_paths(tmp_path, stamp="20260425T010203Z")

    assert report_path.name == "research_eval_20260425T010203Z.md"
    assert json_path.name == "research_eval_20260425T010203Z.json"
    assert report_path.parent.name == "live_runs"


def test_package_version_cases_are_excluded_from_deep_research_live_eval():
    case = ResearchEvalCase.from_dict(
        {
            "id": "fast_vite_version",
            "query": "current Vite version",
            "expected_route": "fast_search",
            "category": "fast_search",
        }
    )

    assert case.is_package_version_case is True
