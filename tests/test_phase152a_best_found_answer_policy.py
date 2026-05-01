from __future__ import annotations

import unittest

from taos.core.research.research_pipeline import build_rumour_no_confirmation_answer
from taos.orchestration.finalization_pipeline import FinalizationPipeline


class _FakeEngine:
    def __init__(self, meaning_frame: dict, source_rows: list[dict] | None = None, primary_queries: list[str] | None = None) -> None:
        self._trace_data = {
            "meaning_frame": dict(meaning_frame),
            "universal_understanding": {"meaning_frame": dict(meaning_frame)},
            "evidence_stats": {
                "source_rows": list(source_rows or []),
                "query_plan_summary": {"primary_queries": list(primary_queries or [])},
            },
        }


class Phase152ABestFoundAnswerPolicyTests(unittest.TestCase):
    def test_unconfirmed_claim_with_related_evidence_returns_what_i_found(self) -> None:
        answer = build_rumour_no_confirmation_answer(
            query="india going to block claudde because of new model issue",
            evidence_rows=[
                {
                    "title": "Reuters on Claude Mythos review",
                    "snippet": "India/RBI and banks are reviewing cybersecurity risks around Claude Mythos.",
                },
                {
                    "title": "Anthropic supported countries",
                    "snippet": "Claude remains available in India on the supported countries page.",
                },
            ],
            checked_queries=["India RBI Claude Mythos cybersecurity risks Anthropic"],
        )

        self.assertIn("Rumour status: Not confirmed", answer)
        self.assertIn("What I found:", answer)
        self.assertIn("Current best-supported answer:", answer)
        self.assertIn("What to check next:", answer)

    def test_answer_is_not_only_i_could_not_confirm(self) -> None:
        answer = build_rumour_no_confirmation_answer(
            query="india going to block claudde because of new model issue",
            evidence_rows=[{"title": "Related story", "snippet": "A related story exists."}],
        )
        self.assertIn("What I found:", answer)
        self.assertNotEqual(answer.strip(), "I could not confirm the rumour: india going to block claudde because of new model issue.")

    def test_source_of_record_contradiction_returns_best_supported_status(self) -> None:
        answer = build_rumour_no_confirmation_answer(
            query="india going to block claudde because of new model issue",
            evidence_rows=[
                {
                    "title": "Anthropic supported countries",
                    "snippet": "Claude remains available in India on the supported countries page.",
                }
            ],
        )
        self.assertIn("Current access/source-of-record status:", answer)
        self.assertIn("Available/support-page evidence indicates Claude still appears available in India.", answer)

    def test_no_evidence_returns_searched_and_verification_guidance(self) -> None:
        answer = build_rumour_no_confirmation_answer(
            query="india going to block claudde because of new model issue",
            evidence_rows=[],
            checked_queries=["site:anthropic.com Claude supported countries India"],
        )
        self.assertIn("Sources checked:", answer)
        self.assertIn("What to check next:", answer)
        self.assertIn("Confidence", answer)

    def test_claude_india_rumour_mentions_claude_not_cloud_services(self) -> None:
        answer = build_rumour_no_confirmation_answer(
            query="india going to block claudde because of new model issue",
            evidence_rows=[{"title": "Related Reuters story", "snippet": "Claude Mythos risk review story."}],
        )
        self.assertIn("Claude", answer)
        self.assertNotIn("cloud services", answer.lower())

    def test_finalization_best_found_fallback_uses_related_evidence(self) -> None:
        meaning = {
            "original_query": "india going to block claudde because of new model issue",
            "normalized_query": "Is India going to block Claude because of a new model issue?",
            "user_intent": "rumour_verification",
            "primary_subject": "Claude",
            "protected_entities": ["Claude", "Anthropic"],
            "disallowed_subject_drifts": ["cloud services", "cloud computing"],
        }
        engine = _FakeEngine(
            meaning,
            source_rows=[
                {"title": "Reuters", "snippet": "India/RBI and banks are reviewing Claude Mythos cybersecurity risks."},
                {"title": "Anthropic supported countries", "snippet": "Claude remains available in India."},
            ],
            primary_queries=["India RBI Claude Mythos cybersecurity risks Anthropic"],
        )
        result = FinalizationPipeline()._apply_meaning_frame_authority(
            engine=engine,
            result={
                "answer": "India may block cloud services because of data issues.",
                "formatted_response": "India may block cloud services because of data issues.",
                "trace": {"meaning_frame": meaning, "evidence_stats": {"source_rows": engine._trace_data["evidence_stats"]["source_rows"]}},
                "warnings": [],
                "metadata": {},
                "trust_block": {"confidence": "High"},
            },
        )

        self.assertIn("What I found:", result["answer"])
        self.assertIn("Claude", result["answer"])
        self.assertTrue(result["trace"]["related_evidence_used"])
        self.assertGreater(result["trace"]["related_evidence_sources_count"], 0)


if __name__ == "__main__":
    unittest.main()
