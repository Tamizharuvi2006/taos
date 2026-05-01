from __future__ import annotations

import unittest

from taos.core.research import ResearchAnswerMode, ResearchPipeline
from taos.core.understanding.entity_resolver import EntityResolver
from taos.core.understanding.universal_understanding_gateway import normalize_for_universal_routes


class Phase151AEvidenceZeroEntityLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = ResearchPipeline()

    def test_claudde_resolves_to_claude_not_cloud(self) -> None:
        mention = EntityResolver().best_match("claudde")
        normalized, candidates = normalize_for_universal_routes(
            "india is going to block claudde because of new model issue"
        )

        self.assertIsNotNone(mention)
        self.assertEqual(mention.canonical, "Claude")
        self.assertIn("Claude", normalized)
        self.assertNotIn("cloud services", normalized.lower())
        self.assertTrue(any(item.candidate == "Claude" for item in candidates))

    def test_claude_rumour_query_plan_includes_official_and_mythos_lanes(self) -> None:
        variants = self.pipeline.build_query_variants(
            "research that i got an news that india is going to block claudde because of an new model issue i need details"
        )

        self.assertIn("Anthropic Claude supported countries India", variants)
        self.assertIn("site:anthropic.com Claude supported countries India", variants)
        self.assertIn("Claude API supported regions India", variants)
        self.assertIn("India Claude Mythos cybersecurity risks RBI Anthropic", variants)
        self.assertIn("India blocking Claude Anthropic", variants)
        self.assertIn("India warning banks Claude Mythos", variants)

    def test_zero_selected_rows_cannot_produce_high_confidence_mode(self) -> None:
        policy = self.pipeline.answer_policy(
            quality_summary={"usable_count": 1, "official_source_count": 1, "trusted_source_count": 1},
            citation_coverage={"coverage": 0.0, "claims_supported": 0, "claims_unsupported": 1},
        )

        self.assertEqual(policy["answer_mode"], ResearchAnswerMode.RELATED_EVIDENCE_ONLY.value)
        self.assertTrue(policy["fallback_required"])

    def test_zero_coverage_answer_uses_rumour_not_confirmed_contract(self) -> None:
        policy = self.pipeline.answer_policy(
            quality_summary={"usable_count": 1, "official_source_count": 1, "trusted_source_count": 1},
            citation_coverage={"coverage": 0.0, "claims_supported": 0, "claims_unsupported": 1},
        )
        answer = self.pipeline.compose_research_answer(
            query="india going to block claudde because of new model issue",
            draft_answer="India may block cloud services because of data issues.",
            evidence_rows=[
                {
                    "title": "Reuters on India banks and Claude Mythos review",
                    "snippet": "Indian banks and regulators are reviewing cybersecurity concerns related to Claude Mythos.",
                    "link": "https://www.reuters.com/example",
                    "source_tier": "reporting",
                    "usable_for_research": True,
                }
            ],
            answer_policy=policy,
        )

        self.assertIn("Rumour status: Not confirmed", answer)
        self.assertIn("Best-supported related finding:", answer)
        self.assertIn("What this does NOT prove:", answer)
        self.assertNotIn("The best-supported answer is: India may block cloud services", answer)
        self.assertNotIn("Confidence\nHigh", answer)

    def test_cloud_services_query_remains_cloud(self) -> None:
        normalized, candidates = normalize_for_universal_routes(
            "india cloud services compliance issue for cloud computing providers"
        )

        self.assertIn("cloud services", normalized.lower())
        self.assertFalse(any(item.candidate == "Claude" for item in candidates))

    def test_related_evidence_fallback_mentions_related_story_not_block(self) -> None:
        answer = self.pipeline.compose_rumour_no_confirmation_answer(
            query="india going to block claudde because of new model issue",
            evidence_rows=[
                {
                    "title": "Claude supported countries",
                    "snippet": "Anthropic lists Claude access availability in supported countries including India.",
                    "link": "https://anthropic.com/supported-countries",
                    "source_tier": "official",
                },
                {
                    "title": "Claude Mythos review",
                    "snippet": "Indian regulators and banks are reviewing cybersecurity risks related to Claude Mythos.",
                    "link": "https://www.reuters.com/example",
                    "source_tier": "reporting",
                },
            ],
        )

        self.assertIn("Rumour status: Not confirmed", answer)
        self.assertIn("Current access status:", answer)
        self.assertIn("related", answer.lower())
        self.assertIn("does NOT prove", answer)


if __name__ == "__main__":
    unittest.main()
