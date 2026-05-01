from __future__ import annotations

import unittest

from taos.core.research.no_result_handler import NoResultHandler
from taos.core.routing.route_decider import RouteDecider
from taos.core.search.query_planner_v2 import SearchQueryPlannerV2
from taos.core.understanding import UniversalUnderstandingGateway, detect_subject_drift, enforce_meaning_frame
from taos.orchestration.finalization_pipeline import FinalizationPipeline


class _FakeEngine:
    def __init__(self, meaning_frame: dict) -> None:
        self._trace_data = {
            "meaning_frame": dict(meaning_frame),
            "universal_understanding": {"meaning_frame": dict(meaning_frame)},
        }


class Phase151A3MeaningFrameAuthorityTests(unittest.TestCase):
    def test_claude_query_builds_authoritative_meaning_frame(self) -> None:
        frame = UniversalUnderstandingGateway().understand(
            "india going to block claudde because of new model issue"
        )
        meaning = frame.meaning_frame

        self.assertIsNotNone(meaning)
        self.assertEqual(meaning.primary_subject, "Claude")
        self.assertIn("Claude", meaning.protected_entities)
        self.assertIn("Anthropic", meaning.protected_entities)
        self.assertIn("cloud services", meaning.disallowed_subject_drifts)
        self.assertEqual(meaning.claim_type, "access_block_claim")

    def test_search_plan_uses_claude_not_cloud_services(self) -> None:
        plan = SearchQueryPlannerV2().plan(
            "india going to block claudde because of new model issue"
        )
        flattened = plan.flatten(include_fallback=True)
        joined = " | ".join(flattened).lower()

        self.assertIn("claude", joined)
        self.assertIn("anthropic", joined)
        self.assertNotIn("cloud services", joined)

    def test_subject_drift_guard_blocks_cloud_services_answer(self) -> None:
        frame = UniversalUnderstandingGateway().understand(
            "india going to block claudde because of new model issue"
        )
        answer = "India may block cloud services because of data security and compliance."

        self.assertTrue(detect_subject_drift(answer, frame.meaning_frame))
        corrected, drifted = enforce_meaning_frame(answer, frame.meaning_frame)
        self.assertTrue(drifted)
        self.assertIn("Claude", corrected)
        self.assertNotIn("cloud services", corrected.lower())

    def test_zero_evidence_fallback_mentions_claude_not_cloud(self) -> None:
        result = NoResultHandler().build(
            goal="india going to block claudde because of new model issue",
            checked_queries=[],
        )
        answer = str(result["answer"])
        self.assertIn("Claude", answer)
        self.assertIn("Not confirmed", answer)
        self.assertNotIn("cloud services", answer.lower())

    def test_real_cloud_query_remains_cloud_services(self) -> None:
        frame = UniversalUnderstandingGateway().understand("India cloud services regulation")
        meaning = frame.meaning_frame

        self.assertIsNotNone(meaning)
        self.assertNotEqual(meaning.primary_subject, "Claude")
        self.assertNotIn("cloud services", " ".join(meaning.disallowed_subject_drifts).lower())

    def test_finalization_guard_marks_subject_drift_in_trace(self) -> None:
        frame = UniversalUnderstandingGateway().understand(
            "india going to block claudde because of new model issue"
        )
        engine = _FakeEngine(frame.meaning_frame.as_dict())
        pipeline = FinalizationPipeline()
        guarded = pipeline._apply_meaning_frame_authority(
            engine=engine,
            result={
                "answer": "India may block cloud services soon.",
                "formatted_response": "India may block cloud services soon.",
                "trace": {"meaning_frame": frame.meaning_frame.as_dict()},
                "metadata": {},
                "warnings": [],
                "trust_block": {"confidence": "High"},
            },
        )

        self.assertTrue(guarded["trace"]["subject_drift_detected"])
        self.assertTrue(guarded["metadata"]["subject_drift_detected"])
        self.assertEqual(guarded["trust_block"]["confidence"], "Low")
        self.assertIn("Claude", guarded["answer"])

    def test_low_confidence_subject_ambiguity_routes_clarification(self) -> None:
        decision = RouteDecider().decide_sync_for_tests("claud issue")
        self.assertEqual(decision.route, "clarification")

    def test_package_version_path_remains_unaffected(self) -> None:
        decision = RouteDecider().decide_sync_for_tests("curent vite versio")
        signals = decision.routing_signals["universal_understanding"]

        self.assertEqual(decision.route, "fast_search")
        self.assertEqual(signals["relation"], "latest_version")


if __name__ == "__main__":
    unittest.main()
