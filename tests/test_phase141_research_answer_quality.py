from __future__ import annotations

import unittest

from taos.core.research import ResearchPipeline


class Phase141ResearchAnswerQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = ResearchPipeline()

    def test_rumour_unconfirmed_with_related_evidence_is_useful(self) -> None:
        rows = [
            {
                "title": "Anthropic supported countries",
                "link": "https://support.anthropic.com/en/articles/8461763-supported-countries",
                "snippet": "Anthropic lists Claude as supported and available in India.",
                "tier": "official",
                "published_at": "2026-04-28",
            },
            {
                "title": "Reuters: RBI reviews Claude Mythos cyber risks",
                "link": "https://www.reuters.com/example",
                "snippet": "India's RBI and banks are reviewing cybersecurity risks around Anthropic's Claude Mythos model.",
                "tier": "reporting",
                "published_at": "2026-04-29",
            },
        ]
        quality = self.pipeline.assess_source_quality(rows=rows, query="india blocking claude rumour", official_source_required=True)
        policy = self.pipeline.answer_policy(
            quality_summary=quality["summary"],
            citation_coverage={"coverage": 0.0, "claims_supported": 0},
            conflict_summary={},
        )
        answer = self.pipeline.compose_research_answer(
            query="india blocking claude rumour",
            evidence_rows=quality["usable_rows"],
            answer_policy=policy,
        )

        self.assertIn("Rumour status: Not confirmed", answer)
        self.assertIn("Best-supported status", answer)
        self.assertIn("What may be causing confusion", answer)
        self.assertNotEqual(answer.strip(), "I could not confirm the claim about Claude.")
        self.assertTrue(policy["related_evidence_used"])
        self.assertEqual(policy["confidence"], "Low")

    def test_latest_news_answer_exposes_freshness_status(self) -> None:
        rows = [
            {
                "title": "OpenAI changelog",
                "link": "https://openai.com/changelog",
                "snippet": "OpenAI announced current API updates in the changelog.",
                "tier": "official",
                "published_at": "2026-04-29",
            },
            {
                "title": "OpenAI docs models",
                "link": "https://platform.openai.com/docs/models",
                "snippet": "The official models documentation lists current model availability.",
                "tier": "official",
                "published_at": "2026-04-28",
            },
        ]
        quality = self.pipeline.assess_source_quality(
            rows=rows,
            query="latest OpenAI news today",
            freshness_summary={"freshness_score": 0.88},
            official_source_required=True,
        )
        policy = self.pipeline.answer_policy(
            quality_summary=quality["summary"],
            citation_coverage={"coverage": 0.8, "claims_supported": 2},
            conflict_summary={},
        )
        answer = self.pipeline.compose_research_answer(
            query="latest OpenAI news today",
            draft_answer="The best-supported answer is: OpenAI announced current API updates. [S1]",
            evidence_rows=quality["usable_rows"],
            answer_policy=policy,
        )

        self.assertIn("Freshness status", answer)
        self.assertNotIn("unsupported", answer.lower())
        self.assertEqual(policy["freshness_status"], "current_or_recent")

    def test_official_query_separates_official_status(self) -> None:
        rows = [
            {
                "title": "Claude supported countries",
                "link": "https://support.anthropic.com/en/articles/8461763-supported-countries",
                "snippet": "Claude is supported in India according to Anthropic supported countries.",
                "tier": "official",
                "published_at": "2026-04-28",
            },
            {
                "title": "Blog discussing supported regions",
                "link": "https://blog.example.com/claude-india",
                "snippet": "A blog summarizes Claude availability in India.",
                "tier": "other",
                "published_at": "2026-04-22",
            },
        ]
        quality = self.pipeline.assess_source_quality(rows=rows, query="official supported countries Claude India", official_source_required=True)
        policy = self.pipeline.answer_policy(
            quality_summary=quality["summary"],
            citation_coverage={"coverage": 0.8, "claims_supported": 2},
            conflict_summary={},
        )
        answer = self.pipeline.compose_research_answer(
            query="official supported countries Claude India",
            draft_answer="The best-supported answer is: Claude appears supported in India on Anthropic's supported countries page. [S1]",
            evidence_rows=quality["usable_rows"],
            answer_policy=policy,
        )

        self.assertIn("Official status", answer)
        self.assertIn("official/source-of-record", answer.lower())
        self.assertTrue(policy["exact_claim_confirmed"])

    def test_comparison_answer_is_direct_and_structured(self) -> None:
        rows = [
            {
                "title": "React docs",
                "link": "https://react.dev",
                "snippet": "React offers a flexible ecosystem and large hiring pool.",
                "tier": "official",
                "published_at": "2026-04-20",
            },
            {
                "title": "Angular docs",
                "link": "https://angular.dev",
                "snippet": "Angular provides more built-in structure for large teams.",
                "tier": "official",
                "published_at": "2026-04-20",
            },
        ]
        quality = self.pipeline.assess_source_quality(rows=rows, query="react vs angular which better")
        policy = self.pipeline.answer_policy(
            quality_summary=quality["summary"],
            citation_coverage={"coverage": 0.8, "claims_supported": 2},
            conflict_summary={},
        )
        answer = self.pipeline.compose_research_answer(
            query="react vs angular which better",
            draft_answer="The best-supported answer is: React is the better default for flexibility, while Angular is stronger when a team wants more built-in structure. [S1] [S2]",
            evidence_rows=quality["usable_rows"],
            answer_policy=policy,
        )

        self.assertIn("Decision table", answer)
        self.assertIn("Best choice by use case", answer)
        self.assertIn("Trade-offs", answer)

    def test_weak_evidence_case_explains_why_not_to_overclaim(self) -> None:
        rows = [
            {
                "title": "Unknown company blog rumor",
                "link": "https://example.com/startup-rumor",
                "snippet": "A speculative blog claims a small company might be connected to Google.",
                "tier": "other",
                "published_at": "2026-04-21",
            }
        ]
        quality = self.pipeline.assess_source_quality(rows=rows, query="is small unknown company X secretly owned by Google")
        policy = self.pipeline.answer_policy(
            quality_summary=quality["summary"],
            citation_coverage={"coverage": 0.55, "claims_supported": 1},
            conflict_summary={},
        )
        answer = self.pipeline.compose_research_answer(
            query="is small unknown company X secretly owned by Google",
            draft_answer="The best-supported answer is: there is only weak evidence for the ownership claim. [S1]",
            evidence_rows=quality["usable_rows"],
            answer_policy=policy,
        )

        self.assertIn("What to treat carefully", answer)
        self.assertIn("Bottom line", answer)
        self.assertEqual(policy["confidence"], "Low")


if __name__ == "__main__":
    unittest.main()
