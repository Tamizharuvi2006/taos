from __future__ import annotations

import unittest

from taos.core.research import ResearchPipeline
from taos.core.search.search_depth_router import SearchDepthRouter
from taos.orchestration.engine import OrchestrationEngine


class Phase108CDeepResearchQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = ResearchPipeline()

    def test_official_source_query_prefers_firebase_pricing_source(self):
        router = SearchDepthRouter()
        self.assertEqual(router.route("official Firebase pricing update").mode, "official_search")
        rows = [
            {
                "title": "Firebase Pricing",
                "link": "https://firebase.google.com/pricing",
                "snippet": "Firebase pricing lists current plan details and usage-based billing.",
                "published_at": "2026-04-20",
                "rank_score": 0.82,
            },
            {
                "title": "Random Firebase pricing blog",
                "link": "https://seo-example.com/firebase-pricing",
                "snippet": "A blog post about Firebase pricing with generic commentary.",
                "published_at": "2024-01-10",
                "rank_score": 0.45,
            },
        ]
        quality = self.pipeline.assess_source_quality(
            rows=rows,
            query="official Firebase pricing update",
            official_source_required=True,
        )
        summary = quality["summary"]
        self.assertGreaterEqual(summary["official_source_count"], 1)
        self.assertEqual(quality["rows"][0]["source_tier"], "official")

        answer = "Firebase pricing is best checked from the official Firebase pricing page. [S1]"
        coverage = self.pipeline.citation_coverage(
            citation_plan=self.pipeline.plan_citations(answer=answer, source_rows=quality["usable_rows"])
        )
        self.assertGreaterEqual(coverage["coverage"], 0.75)
        self.assertLessEqual(coverage["claims_unsupported"], 0)

    def test_news_current_query_exposes_freshness_metadata(self):
        router = SearchDepthRouter()
        self.assertEqual(router.route("latest OpenAI API model changes").mode, "news_search")
        rows = [
            {
                "title": "OpenAI API model update",
                "link": "https://platform.openai.com/docs/models",
                "snippet": "OpenAI model documentation lists API model availability and recent changes.",
                "published_at": "2026-04-24",
                "rank_score": 0.9,
            },
            {
                "title": "OpenAI changelog",
                "link": "https://openai.com/changelog",
                "snippet": "OpenAI changelog describes recent API model updates.",
                "published_at": "2026-04-22",
                "rank_score": 0.8,
            },
        ]
        quality = self.pipeline.assess_source_quality(
            rows=rows,
            query="latest OpenAI API model changes",
            freshness_summary={"freshness_score": 0.72, "booster": {"boosted": True}},
            official_source_required=True,
        )
        summary = quality["summary"]
        self.assertGreaterEqual(summary["freshness_score"], 0.7)
        self.assertTrue(summary["freshness_booster_used"])
        self.assertFalse(summary["stale_detected"])

    def test_comparison_query_has_diverse_react_vue_sources(self):
        router = SearchDepthRouter()
        self.assertEqual(router.route("compare React 19 and Vue latest").mode, "comparison_search")
        rows = [
            {
                "title": "React 19 release",
                "link": "https://react.dev/blog/2024/12/05/react-19",
                "snippet": "React 19 release notes describe actions, use API, and related changes.",
                "published_at": "2024-12-05",
                "rank_score": 0.91,
            },
            {
                "title": "Vue latest release",
                "link": "https://vuejs.org/about/releases.html",
                "snippet": "Vue release information lists latest stable versions and framework updates.",
                "published_at": "2026-03-15",
                "rank_score": 0.86,
            },
        ]
        selection = self.pipeline.select_evidence(rows=rows, query="compare React 19 and Vue latest", limit=4)
        quality = self.pipeline.assess_source_quality(rows=selection["rows"], query="compare React 19 and Vue latest")
        domains = {row["domain"] for row in quality["usable_rows"]}
        source_diversity = len(domains) / max(1, len(quality["usable_rows"]))
        self.assertGreaterEqual(source_diversity, 0.6)

    def test_weak_evidence_returns_cautious_partial_answer_not_generic_failure(self):
        engine = OrchestrationEngine()
        rows = [
            {
                "title": "Small startup model rumor",
                "link": "https://example.com/startup-model-rumor",
                "snippet": "A weak but relevant report mentions a possible private startup AI model release.",
                "published_at": "2026-04-23",
                "tier": "other",
                "source_tier": "other",
                "usable_for_research": True,
                "extraction_quality": 0.42,
            }
        ]
        answer = engine._build_research_evidence_fallback(
            "latest unknown private startup AI model release",
            rows,
            freshness_mode=True,
            agreement={"agreement_level": "low", "conflict_detected": False, "stale_detected": False},
        )
        self.assertTrue(answer)
        self.assertTrue(answer.startswith("Answer"))
        self.assertIn("best-supported answer", answer)
        self.assertNotIn("I couldn't verify this confidently", answer)

    def test_conflict_query_surfaces_conflict_and_adjusts_confidence(self):
        router = SearchDepthRouter()
        self.assertEqual(router.route("compare current best vector databases for RAG").mode, "comparison_search")
        rows = [
            {
                "title": "Vector database benchmark 2026",
                "link": "https://trusted.example.com/vector-db-a",
                "snippet": "Vector database benchmark 2026 reports Pinecone latency is 10 ms for RAG workloads.",
                "tier": "trusted",
                "source_quality": 0.8,
            },
            {
                "title": "Vector database benchmark 2026",
                "link": "https://other.example.com/vector-db-b",
                "snippet": "Vector database benchmark 2026 reports Pinecone latency is 30 ms for RAG workloads.",
                "tier": "trusted",
                "source_quality": 0.75,
            },
        ]
        conflict = self.pipeline.resolve_conflicts(evidence_rows=rows)["summary"]
        policy = self.pipeline.answer_policy(
            quality_summary={"usable_count": 2, "trusted_source_count": 2, "official_source_count": 0},
            citation_coverage={"coverage": 0.8},
            conflict_summary=conflict,
        )
        self.assertTrue(conflict["conflict_detected"])
        self.assertEqual(conflict["agreement_level"], "mixed")
        self.assertTrue(policy["conflict_detected"])
        self.assertEqual(policy["answer_mode"], "partial_but_useful")


if __name__ == "__main__":
    unittest.main()
