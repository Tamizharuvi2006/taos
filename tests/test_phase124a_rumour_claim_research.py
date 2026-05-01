from __future__ import annotations

import unittest

from taos.core.research.no_result_handler import NoResultHandler
import taos.core.research.research_pipeline as research_pipeline
from taos.core.research.research_pipeline import ResearchPipeline
from taos.core.search.search_depth_router import SearchDepthRouter


class Phase124ARumourClaimResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = ResearchPipeline()

    def test_noisy_lovking_claude_query_extracts_structured_claim_and_queries(self) -> None:
        query = "research that i got an news that india is lovking claude i got an rumour"

        claim = self.pipeline.normalize_rumour_claim(query)
        variants = self.pipeline.build_query_variants(query)

        self.assertTrue(claim["detected"])
        self.assertEqual(claim["subject"], "India")
        self.assertEqual(claim["object"], "Claude")
        self.assertEqual(claim["relation"], "blocking")
        self.assertEqual(claim["normalized_query"], "Is Claude blocked or restricted in India?")
        self.assertIn("blocking", claim["relation_variants"])
        self.assertIn("locking", claim["relation_variants"])
        self.assertIn("India blocking Claude Anthropic", variants)
        self.assertIn("India ban Claude AI", variants)
        self.assertIn("Claude unavailable in India Anthropic", variants)
        self.assertIn("Anthropic Claude supported countries India", variants)
        self.assertIn("site:anthropic.com supported countries Claude India", variants)
        self.assertNotIn(query, variants[:3])

    def test_general_fuzzy_entity_correction_handles_unknown_messy_spelling(self) -> None:
        query = "research that i got an news that indai is lovking claudde i got an rumour"

        understanding = self.pipeline.understand_messy_query(query)
        variants = self.pipeline.build_query_variants(query)

        self.assertEqual(understanding["intent"], "rumour_verification")
        self.assertEqual(understanding["subject"], "India")
        self.assertEqual(understanding["object"], "Claude")
        self.assertEqual(understanding["relation"], "blocked_or_restricted_access")
        self.assertEqual(understanding["raw_query_priority"], "fallback_only")
        self.assertIn("India blocking Claude Anthropic", variants)
        corrected_tokens = {row["candidate"] for row in understanding["correction_candidates"]}
        self.assertTrue({"india", "claude", "blocking"}.issubset(corrected_tokens))

    def test_india_banning_claude_adds_official_supported_countries_query(self) -> None:
        variants = self.pipeline.build_query_variants("i heard india banning claude is it true")

        self.assertIn("Anthropic Claude supported countries India", variants)
        self.assertIn("site:anthropic.com supported countries Claude India", variants)

    def test_openai_blocked_in_india_routes_as_access_claim(self) -> None:
        decision = SearchDepthRouter().route("rumor openai blocked in india")
        claim = self.pipeline.normalize_rumour_claim("rumor openai blocked in india")

        self.assertEqual(decision.mode, "news_search")
        self.assertEqual(decision.reason, "rumour_access_claim_query")
        self.assertTrue(claim["detected"])
        self.assertTrue(claim["access_claim"])
        self.assertEqual(claim["subject"], "India")
        self.assertEqual(claim["object"], "OpenAI")
        self.assertIn("site:openai.com supported countries OpenAI India", claim["queries"])

    def test_lovking_is_inferred_by_fuzzy_relation_not_one_off_typo_table(self) -> None:
        claim = self.pipeline.normalize_rumour_claim("india lovking claude rumor")

        self.assertTrue(claim["detected"])
        self.assertEqual(claim["relation"], "blocking")
        self.assertIn("blocking", claim["relation_variants"])
        self.assertIn("locking", claim["relation_variants"])
        self.assertFalse(hasattr(research_pipeline, "_TYPO_VARIANTS"))

    def test_no_confirmation_with_related_evidence_is_useful_not_generic_failure(self) -> None:
        answer = self.pipeline.compose_rumour_no_confirmation_answer(
            query="research that i got an news that india is lovking claude i got an rumour",
            evidence_rows=[
                {
                    "title": "Claude supported countries",
                    "link": "https://support.anthropic.com/en/articles/8461763-supported-countries",
                    "snippet": "Anthropic lists Claude availability by supported country, including India.",
                    "tier": "official",
                },
                {
                    "title": "Claude outage report",
                    "snippet": "A temporary outage affected Claude access for some users.",
                },
            ],
        )

        self.assertIn("Rumour status: Not confirmed", answer)
        self.assertIn("Best-supported status: Claude still appears available/supported in India based on official source.", answer)
        self.assertIn("What may be causing confusion:", answer)
        self.assertIn("I could not confirm the rumour", answer)
        self.assertIn("The closest related evidence is:", answer)
        self.assertNotEqual(answer.strip(), "I couldn't verify this confidently from reliable sources.")

    def test_no_result_handler_uses_rumour_wording_for_claims(self) -> None:
        result = NoResultHandler().build(
            goal="research that i got an news that india is lovking claude i got an rumour",
            checked_queries=[],
        )

        self.assertTrue(result["metadata"]["rumour_claim"])
        self.assertIn("Rumour status: Not confirmed", result["answer"])
        self.assertIn("Current best-supported status:", result["answer"])
        self.assertNotIn("I couldn't verify this confidently from reliable sources.", result["answer"])

    def test_high_coverage_with_sources_does_not_use_generic_no_result_mode(self) -> None:
        policy = self.pipeline.answer_policy(
            quality_summary={
                "usable_count": 1,
                "official_source_count": 0,
                "trusted_source_count": 0,
            },
            citation_coverage={"coverage": 0.7},
        )
        answer = self.pipeline.compose_research_answer(
            query="rumor openai blocked in india",
            draft_answer="A related source exists, but the exact rumour is not confirmed. [S1]",
            evidence_rows=[
                {
                    "title": "Related access report",
                    "snippet": "A related access report discusses availability in India without confirming a block.",
                    "tier": "other",
                    "usable_for_research": True,
                    "source_tier": "other",
                }
            ],
            answer_policy=policy,
        )

        self.assertIn(policy["answer_mode"], {"best_supported", "partial_but_useful"})
        self.assertFalse(policy["fallback_required"])
        self.assertIn("The best-supported answer is:", answer)
        self.assertNotIn("I could not verify a reliable answer", answer)


if __name__ == "__main__":
    unittest.main()
