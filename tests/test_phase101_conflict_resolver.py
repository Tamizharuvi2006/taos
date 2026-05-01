from __future__ import annotations

import unittest

from taos.core.research.conflict_resolver import ConflictResolver


class Phase101ConflictResolverTests(unittest.TestCase):
    def test_numeric_disagreement_creates_conflict_group(self):
        resolver = ConflictResolver()
        rows = [
            {"title": "Report A", "snippet": "Revenue reached 12% in 2026.", "selection_score": 0.7},
            {"title": "Report B", "snippet": "Revenue reached 18% in 2026.", "selection_score": 0.8},
        ]
        result = resolver.resolve(evidence_rows=rows)
        self.assertGreaterEqual(result["summary"]["conflict_group_count"], 1)

    def test_unresolved_conflict_requires_uncertainty(self):
        resolver = ConflictResolver()
        claims = [
            "The policy was approved in 2026.",
            "The policy was not approved in 2026.",
        ]
        result = resolver.resolve(evidence_rows=[{"title": "A", "snippet": claims[0]}, {"title": "B", "snippet": claims[1]}], claims=claims)
        self.assertTrue(result["summary"]["requires_uncertainty_wording"])


if __name__ == "__main__":
    unittest.main()
