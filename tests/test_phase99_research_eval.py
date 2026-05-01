from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from taos.core.evaluation.research_eval import ResearchEvalCase, ResearchEvalHarness


class _MockRunner:
    async def run_case(self, case: ResearchEvalCase):
        if case.category == "failure":
            return {
                "route": case.expected_route,
                "mode": case.expected_route,
                "answer": "I couldn't verify this from reliable sources.",
                "confidence": 0.2,
                "sources": [],
                "warnings": ["No reliable evidence found."],
                "trust_block": {
                    "citation_coverage": 0.0,
                    "supported_claims": 0,
                    "unsupported_claims": 0,
                    "freshness_summary": {"freshness_score": 0.0, "stale_detected": False},
                },
            }
        return {
            "route": case.expected_route,
            "mode": case.expected_route,
            "answer": f"Answer for {case.query}. " + " ".join(case.expected_behavior),
            "confidence": 0.68,
            "sources": [f"https://a.example.com/{case.id}", f"https://b.example.com/{case.id}"],
            "warnings": [],
            "evidence_matrix_summary": {
                "citation_coverage": 0.8,
                "supported_claims": 3,
                "unsupported_claims": 0,
            },
            "freshness_summary": {
                "freshness_score": 0.85 if case.freshness_required else 0.7,
                "stale_detected": False,
            },
            "trust_block": {
                "citation_coverage": 0.8,
                "supported_claims": 3,
                "unsupported_claims": 0,
                "source_diversity_score": 1.0,
                "freshness_summary": {
                    "freshness_score": 0.85 if case.freshness_required else 0.7,
                    "stale_detected": False,
                },
            },
        }


class ResearchEvalHarnessTests(unittest.TestCase):
    def test_load_cases_and_render_report(self):
        harness = ResearchEvalHarness()
        path = Path("D:\\agent\\taos\\eval\\research_cases.json")
        cases = harness.load_cases(path)
        self.assertGreaterEqual(len(cases), 1)
        self.assertEqual("fast_vite_version", cases[0].id)

        evaluation = asyncio.run(harness.evaluate_cases(cases=cases[:1], runner=_MockRunner()))
        report = harness.render_markdown_report(evaluation)
        self.assertIn("Research Evaluation Report", report)
        self.assertIn("Route Accuracy", report)

    def test_failure_case_rewards_hallucination_resistance(self):
        harness = ResearchEvalHarness()
        case = ResearchEvalCase(
            id="failure1",
            query="fake company",
            expected_route="fast_search",
            category="failure",
            freshness_required=True,
            min_sources=0,
            expected_behavior=["reliable sources"],
            forbidden_behavior=["definitely"],
        )
        evaluation = asyncio.run(harness.evaluate_cases(cases=[case], runner=_MockRunner()))
        result = evaluation["results"][0]
        self.assertEqual(1.0, result["hallucination_resistance"])
        self.assertLess(result["confidence"], 0.3)

    def test_aggregate_scores_include_required_metrics(self):
        harness = ResearchEvalHarness()
        cases = [
            ResearchEvalCase(
                id="a",
                query="current Vite version",
                expected_route="fast_search",
                category="fast_search",
                freshness_required=True,
                min_sources=2,
                expected_behavior=["version"],
                forbidden_behavior=["as of my last update"],
            ),
            ResearchEvalCase(
                id="b",
                query="fake company",
                expected_route="fast_search",
                category="failure",
                freshness_required=True,
                min_sources=0,
                expected_behavior=["reliable sources"],
                forbidden_behavior=["definitely"],
            ),
        ]
        evaluation = asyncio.run(harness.evaluate_cases(cases=cases, runner=_MockRunner()))
        aggregates = evaluation["aggregates"]
        for key in (
            "route_accuracy",
            "groundedness_score",
            "citation_quality",
            "source_diversity",
            "freshness_score",
            "confidence_calibration",
            "hallucination_resistance",
            "latency_score",
            "overall_score",
        ):
            self.assertIn(key, aggregates)


if __name__ == "__main__":
    unittest.main()
