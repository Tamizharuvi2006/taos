from __future__ import annotations

import unittest

from taos.core.safety.high_stakes_research_guard import HighStakesResearchGuard


class Phase104HighStakesResearchTests(unittest.TestCase):
    def test_guard_detects_high_stakes_and_requires_official_sources(self):
        guard = HighStakesResearchGuard()
        result = guard.evaluate_query("medical treatment dosage for adults")
        self.assertTrue(result["high_stakes"])
        self.assertTrue(result["requires_official_source"])

    def test_guard_warns_when_official_source_is_missing(self):
        guard = HighStakesResearchGuard()
        summary = guard.evaluate_evidence(query="legal compliance update", rows=[{"title": "Blog", "provider": "blog.example.com"}])
        self.assertFalse(summary["official_source_found"])
        self.assertIsNotNone(summary["warning"])

    def test_guard_adds_safe_wording(self):
        guard = HighStakesResearchGuard()
        text = guard.apply_safe_wording(answer="Here is the answer.", summary={"high_stakes": True})
        self.assertIn("not professional advice", text.lower())


if __name__ == "__main__":
    unittest.main()
