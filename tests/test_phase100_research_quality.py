from __future__ import annotations

import asyncio
import unittest

from taos.core.research import ResearchPipeline
from taos.core.research.citation_planner import CitationPlanner
from taos.core.research.evidence_selector import EvidenceSelector
from taos.core.research.freshness_booster import FreshnessBooster
from taos.core.research.source_diversity_enforcer import SourceDiversityEnforcer


class Phase100ResearchQualityTests(unittest.TestCase):
    def test_evidence_selector_prefers_quality_and_caps_domain(self):
        selector = EvidenceSelector()
        rows = [
            {"title": "Official update", "link": "https://example.gov/a", "snippet": "Policy updated in 2026.", "tier": "official", "source_quality": 0.95, "freshness_score": 0.9, "extract_quality_score": 0.8},
            {"title": "Official duplicate", "link": "https://example.gov/b", "snippet": "Policy updated in 2026.", "tier": "official", "source_quality": 0.92, "freshness_score": 0.88, "extract_quality_score": 0.7},
            {"title": "Official extra", "link": "https://example.gov/c", "snippet": "Policy updated in 2026.", "tier": "official", "source_quality": 0.9, "freshness_score": 0.86, "extract_quality_score": 0.75},
            {"title": "Trusted analysis", "link": "https://news.example.com/x", "snippet": "Analysis of the updated policy.", "tier": "trusted", "source_quality": 0.72, "freshness_score": 0.7, "extract_quality_score": 0.6},
        ]
        result = selector.select(rows, query="updated policy 2026", limit=4, max_per_domain=2)
        kept = result["rows"]
        self.assertEqual(len([row for row in kept if "example.gov" in row.get("source_domain", "")]), 2)
        self.assertGreaterEqual(result["summary"]["selected_count"], 2)

    def test_citation_planner_flags_unsupported_factual_paragraph(self):
        planner = CitationPlanner()
        answer = "OpenAI released a 2026 policy update. Mars office launched in 2026."
        source_rows = [{"title": "OpenAI policy", "snippet": "OpenAI released a policy update in 2026.", "provider": "openai.com"}]
        result = planner.plan(answer=answer, source_rows=source_rows)
        self.assertGreaterEqual(result["summary"]["unsupported_factual_paragraphs"], 1)

    def test_diversity_enforcer_caps_two_per_domain(self):
        enforcer = SourceDiversityEnforcer()
        rows = [
            {"title": "A1", "link": "https://same.com/1", "selection_score": 0.9},
            {"title": "A2", "link": "https://same.com/2", "selection_score": 0.8},
            {"title": "A3", "link": "https://same.com/3", "selection_score": 0.7},
            {"title": "B1", "link": "https://other.com/1", "selection_score": 0.6},
        ]
        result = enforcer.enforce(rows, max_per_domain=2, limit=4)
        self.assertEqual(result["summary"]["domain_counts"]["same.com"], 2)


class Phase100FreshnessBoosterTests(unittest.IsolatedAsyncioTestCase):
    async def test_freshness_booster_runs_only_for_weak_freshness(self):
        booster = FreshnessBooster(threshold=0.8)

        async def fake_search(**kwargs):
            return {
                "results": [
                    {"title": "Latest update", "link": "https://fresh.example.com/1", "snippet": "Latest official update today."}
                ]
            }

        result = await booster.boost(
            query="latest company policy today",
            rows=[{"title": "Old source", "link": "https://old.example.com/1", "snippet": "Old report"}],
            freshness_summary={"freshness_score": 0.2},
            web_search_fn=fake_search,
            search_type="news",
            recency_days=2,
        )
        self.assertTrue(result["summary"]["boosted"])
        self.assertEqual(result["summary"]["added_rows"], 1)


class Phase100ResearchPipelineTests(unittest.TestCase):
    def test_research_pipeline_softens_unsupported_claims(self):
        pipeline = ResearchPipeline()
        answer = "OpenAI released a 2026 policy update. Mars office launched in 2026."
        rows = [{"title": "OpenAI update", "snippet": "OpenAI released a 2026 policy update.", "provider": "openai.com"}]
        citation_plan = pipeline.plan_citations(answer=answer, source_rows=rows)
        softened = pipeline.soften_unsupported_claims(answer=answer, citation_plan=citation_plan)
        self.assertIn("tentative", softened.lower())


if __name__ == "__main__":
    unittest.main()
