from __future__ import annotations

import unittest

from taos.core.research import ResearchPipeline
from taos.core.research.claim_verifier import ClaimVerifier
from taos.core.research.evidence_threshold_gate import EvidenceThresholdGate
from taos.core.search.result_prefilter import ResultPrefilter
from taos.core.search.search_accuracy_engine import SearchAccuracyEngine


class Phase152SearchAccuracyEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SearchAccuracyEngine()
        self.pipeline = ResearchPipeline()

    def test_claude_rumour_query_creates_core_lanes(self) -> None:
        bundle = self.engine.build_plan(
            "reserch that i got an news that india is going to block claudde because of an new model issue i need details"
        )
        lanes = bundle["summary"]["lanes"]

        self.assertTrue(lanes["official"])
        self.assertTrue(lanes["news"])
        self.assertTrue(lanes["contradiction"])
        self.assertTrue(lanes["background"])

    def test_primary_queries_use_claude_not_cloud_services(self) -> None:
        bundle = self.engine.build_plan(
            "reserch that i got an news that india is going to block claudde because of an new model issue i need details"
        )
        joined = " | ".join(bundle["summary"]["primary_queries"]).lower()

        self.assertIn("claude", joined)
        self.assertIn("anthropic", joined)
        self.assertNotIn("cloud services", joined)

    def test_raw_typo_query_is_fallback_not_primary(self) -> None:
        bundle = self.engine.build_plan(
            "reserch that i got an news that india is going to block claudde because of an new model issue i need details"
        )
        raw = "reserch that i got an news that india is going to block claudde because of an new model issue i need details"

        self.assertNotIn(raw, bundle["summary"]["primary_queries"])
        self.assertIn(raw, bundle["summary"]["fallback_queries"])

    def test_explicit_cloud_query_stays_cloud(self) -> None:
        bundle = self.engine.build_plan("India cloud services regulation")
        meaning = bundle["summary"]["meaning_frame"]
        joined = " | ".join(bundle["summary"]["primary_queries"]).lower()

        self.assertNotEqual(meaning.get("primary_subject"), "Claude")
        self.assertNotIn("claude", joined)

    def test_junk_pages_are_rejected(self) -> None:
        prefilter = ResultPrefilter().filter(
            rows=[
                {"link": "https://example.com/tag/claude", "title": "Claude tag page", "snippet": "tag archive"},
                {"link": "https://anthropic.com/supported-countries", "title": "Supported countries", "snippet": "Claude is available in India."},
                {"link": "https://seo.example.com/best-hosting", "title": "Best hosting", "snippet": "best hosting and cloud services deals"},
            ],
            primary_subject="Claude",
            disallowed_subject_drifts=("cloud services", "cloud computing"),
        )

        self.assertEqual(len(prefilter["kept"]), 1)
        self.assertGreaterEqual(prefilter["rejected_results_count"], 2)

    def test_weak_evidence_triggers_targeted_retry(self) -> None:
        summary = self.pipeline.search_plan_summary("india blocking claudde rumor")
        retry = self.pipeline.targeted_retry_plan(
            query="india blocking claudde rumor",
            quality_summary={"usable_count": 0, "official_source_found": False},
            query_plan_summary=summary,
        )

        self.assertTrue(retry["targeted_retry_used"])
        self.assertTrue(any("anthropic.com" in query or "rbi" in query.lower() for query in retry["queries"]))

    def test_zero_evidence_blocks_factual_synthesis(self) -> None:
        gate = EvidenceThresholdGate().evaluate(
            usable_sources_count=0,
            selected_rows=0,
            coverage=0.0,
            claim_verification_status="no_usable_evidence",
        )
        self.assertFalse(gate["evidence_threshold_passed"])
        self.assertEqual(gate["answer_mode"], "no_usable_evidence")

    def test_related_evidence_only_returns_rumour_status_not_direct_claim(self) -> None:
        verifier = ClaimVerifier().verify(
            query="india going to block claudde because of new model issue",
            evidence_rows=[
                {
                    "title": "Claude Mythos risk review",
                    "snippet": "Indian regulators are reviewing Claude Mythos cybersecurity concerns.",
                }
            ],
        )
        gate = EvidenceThresholdGate().evaluate(
            usable_sources_count=1,
            selected_rows=1,
            coverage=0.0,
            claim_verification_status=verifier["claim_verification_status"],
        )

        self.assertEqual(verifier["claim_verification_status"], "related_evidence_only")
        self.assertEqual(gate["answer_mode"], "related_evidence_only")

    def test_claim_verifier_separates_exact_claim_from_related_evidence(self) -> None:
        contradicted = ClaimVerifier().verify(
            query="india going to block claudde because of new model issue",
            evidence_rows=[
                {
                    "title": "Supported countries",
                    "snippet": "Claude remains available in India on Anthropic supported countries list.",
                }
            ],
        )
        self.assertEqual(contradicted["claim_verification_status"], "contradicted")

    def test_query_plan_summary_appears_with_lanes_used(self) -> None:
        summary = self.pipeline.search_plan_summary(
            "reserch that i got an news that india is going to block claudde because of an new model issue i need details"
        )
        self.assertIn("lanes", summary)
        self.assertIn("lanes_used", summary)
        self.assertTrue(summary["lanes_used"])


if __name__ == "__main__":
    unittest.main()
