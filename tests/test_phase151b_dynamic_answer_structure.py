from __future__ import annotations

import unittest

from taos.core.answering import ResearchAnswerComposerV2, render_sections, select_schema_key


class Phase151BDynamicAnswerStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.composer = ResearchAnswerComposerV2()

    def test_rumour_claim_gets_rumour_headings(self) -> None:
        payload = self.composer.compose_payload(
            query="is india blocking claudde",
            intent="rumour_verification",
            best_supported="The exact block claim is not confirmed.",
            evidence_rows=[{"title": "Related Reuters story", "snippet": "India is reviewing Claude Mythos cyber risks."}],
            status="not_confirmed",
            answer_mode="related_evidence_only",
            route="news_search",
        )

        titles = [section["title"] for section in payload["sections"]]
        self.assertEqual(payload["schema_key"], "related_evidence_only")
        self.assertIn("Claim status", titles)
        self.assertIn("Closest related evidence", titles)
        self.assertNotIn("Answer", titles)

    def test_no_evidence_gets_no_evidence_headings(self) -> None:
        payload = self.composer.compose_payload(
            query="is some new claim true",
            intent="rumour_verification",
            best_supported="No usable evidence was available.",
            evidence_rows=[],
            status="unclear",
            answer_mode="no_usable_evidence",
            route="news_search",
        )

        titles = [section["title"] for section in payload["sections"]]
        self.assertEqual(payload["schema_key"], "no_usable_evidence")
        self.assertEqual(titles, ["What I could not verify", "What I checked", "Best next checks", "Confidence"])

    def test_package_version_gets_source_of_record_headings(self) -> None:
        payload = self.composer.compose_payload(
            query="latest vite version",
            intent="current_lookup",
            best_supported="Vite 9.0.0",
            evidence_rows=[{"title": "npm registry latest tag"}],
            answer_mode="verified",
            route="fast_search",
        )
        titles = [section["title"] for section in payload["sections"]]
        self.assertEqual(payload["schema_key"], "package_version")
        self.assertEqual(titles[:3], ["Latest version", "Source-of-record", "Confidence"])

    def test_comparison_gets_comparison_headings(self) -> None:
        payload = self.composer.compose_payload(
            query="react vs vue which is better",
            intent="comparison",
            best_supported="React is better for this use case.",
            evidence_rows=[{"title": "Comparison source", "snippet": "React and Vue have different trade-offs."}],
            answer_mode="best_supported",
            route="comparison_search",
        )
        titles = [section["title"] for section in payload["sections"]]
        self.assertEqual(payload["schema_key"], "comparison")
        self.assertIn("Quick verdict", titles)
        self.assertIn("Trade-offs", titles)

    def test_troubleshooting_gets_fix_headings(self) -> None:
        payload = self.composer.compose_payload(
            query="nextjs build failed fix",
            intent="current_lookup",
            best_supported="A stale cache is the likely cause.",
            caveats=["Clear the build cache and rebuild."],
            answer_mode="best_supported",
            route="task",
        )
        self.assertEqual(payload["schema_key"], "troubleshooting")
        self.assertEqual(
            [section["title"] for section in payload["sections"]],
            ["Likely cause", "Fix", "Why it works", "If it still fails"],
        )

    def test_renderer_preserves_new_dynamic_headings(self) -> None:
        text = render_sections(
            [
                {"key": "rumour_status", "title": "Rumour status", "content": "Not confirmed.", "bullets": []},
                {"key": "best_supported_status", "title": "Best-supported status", "content": "Exact block claim not confirmed.", "bullets": []},
                {"key": "what_this_does_not_prove", "title": "What this does NOT prove", "content": "It does not prove an India-wide block.", "bullets": []},
            ]
        )
        self.assertIn("Rumour status", text)
        self.assertIn("Best-supported status", text)
        self.assertIn("What this does NOT prove", text)

    def test_schema_selector_never_maps_zero_evidence_to_best_supported_schema(self) -> None:
        schema = select_schema_key(
            intent="rumour_verification",
            route="news_search",
            answer_mode="no_usable_evidence",
            evidence_state="no_usable_evidence",
            query="heard a claim",
        )
        self.assertEqual(schema, "no_usable_evidence")


if __name__ == "__main__":
    unittest.main()
