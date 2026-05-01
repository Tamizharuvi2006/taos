from __future__ import annotations

import unittest

from taos.core.research.no_result_handler import NoResultHandler
from taos.core.research.research_pipeline import ResearchPipeline
from taos.core.understanding import (
    EntityResolver,
    RelationInferencer,
    SearchIntentPlanner,
    SemanticQueryRewriter,
    normalize_messy_query,
)


class Phase124AMessyQueryUnderstandingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = SearchIntentPlanner()
        self.pipeline = ResearchPipeline()

    def test_messy_rumour_builds_structured_intent_frame_and_search_plan(self) -> None:
        frame = self.planner.plan("I heard some rumour that India blocked Claude, is it true?")

        self.assertEqual(frame.original_query, "I heard some rumour that India blocked Claude, is it true?")
        self.assertEqual(frame.intent, "rumour_verification")
        self.assertEqual(frame.entities["country"], "India")
        self.assertEqual(frame.entities["product"], "Claude")
        self.assertEqual(frame.entities["company"], "Anthropic")
        self.assertEqual(frame.relation, "blocked_or_restricted_access")
        self.assertEqual(frame.normalized_question, "Is Claude blocked or restricted in India?")
        self.assertIn("Anthropic Claude supported countries India", frame.search_plan.official)
        self.assertIn("site:anthropic.com Claude supported countries India", frame.search_plan.official)
        self.assertIn("India blocking Claude Anthropic", frame.search_plan.news)
        self.assertIn("Claude available in India Anthropic", frame.search_plan.contradiction)
        self.assertIn("Claude India service issue", frame.search_plan.background)

    def test_raw_typo_heavy_query_is_fallback_not_primary(self) -> None:
        query = "research that i got an news that indai is lovking claudde i got an rumour"

        frame = self.planner.plan(query)
        variants = self.pipeline.build_query_variants(query)

        self.assertEqual(frame.raw_query_priority, "fallback_only")
        self.assertEqual(frame.entities["country"], "India")
        self.assertEqual(frame.entities["product"], "Claude")
        self.assertEqual(frame.relation, "blocked_or_restricted_access")
        self.assertIn("India blocking Claude Anthropic", variants[:6])
        self.assertNotIn(query, variants[:5])
        self.assertEqual(variants[-1], query)

    def test_normalizer_removes_conversational_filler(self) -> None:
        cleaned = normalize_messy_query("research that i got an news that india blocked claude is it true")

        self.assertEqual(cleaned, "india blocked claude")

    def test_entity_resolver_handles_country_company_product_and_typos(self) -> None:
        candidates = EntityResolver().correction_candidates(["indai", "claudde", "openia"])
        resolved = {(row.candidate, row.kind) for row in candidates}

        self.assertIn(("india", "country"), resolved)
        self.assertIn(("claude", "product"), resolved)
        self.assertIn(("openai", "company"), resolved)

    def test_relation_inferencer_detects_required_claim_types(self) -> None:
        inferencer = RelationInferencer()

        self.assertEqual(inferencer.infer(["baning"]).relation, "blocked_or_restricted_access")
        self.assertEqual(inferencer.infer(["outage"]).relation, "unavailable_or_outage")
        self.assertEqual(inferencer.infer(["released"]).relation, "released_or_changed")
        self.assertEqual(inferencer.infer(["pricing"]).relation, "pricing_changed")
        self.assertEqual(inferencer.infer(["security"]).relation, "security_concern")

    def test_low_confidence_rewrite_prompt_preserves_original_and_uncertainty(self) -> None:
        frame = self.planner.plan("someone said the ai thing got blocked maybe")
        prompt = SemanticQueryRewriter().low_confidence_prompt(frame)

        self.assertIn("Original: someone said the ai thing got blocked maybe", prompt)
        self.assertIn("Preserve uncertainty", prompt)
        self.assertTrue(frame.needs_llm_rewrite)

    def test_no_confirmation_answer_is_contextual_not_generic_failure(self) -> None:
        result = NoResultHandler().build(
            goal="I heard some rumour that India blocked Claude, is it true?",
            checked_queries=[],
        )

        self.assertIn("Rumour status: Not confirmed", result["answer"])
        self.assertIn("Best-supported status:", result["answer"])
        self.assertIn("Possible confusion:", result["answer"])
        self.assertIn("Sources checked:", result["answer"])
        self.assertIn("Confidence", result["answer"])
        self.assertNotIn("I couldn't verify this confidently from reliable sources.", result["answer"])

    def test_package_source_of_record_query_stays_outside_messy_rumour_mode(self) -> None:
        frame = self.planner.plan("current vite version")

        self.assertNotEqual(frame.intent, "rumour_verification")
        self.assertNotEqual(frame.relation, "blocked_or_restricted_access")


if __name__ == "__main__":
    unittest.main()
