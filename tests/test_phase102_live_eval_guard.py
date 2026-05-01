from __future__ import annotations

import unittest

from taos.core.evaluation.live_eval_guard import LiveEvalGuard


class Phase102LiveEvalGuardTests(unittest.TestCase):
    def test_guard_stops_when_limits_are_exceeded(self):
        guard = LiveEvalGuard(max_cases=1, max_cost_estimate=0.1, max_runtime_seconds=9999, required_env=[])
        self.assertTrue(guard.before_case("case-1", estimated_cost=0.05)["allowed"])
        blocked = guard.before_case("case-2", estimated_cost=0.05)
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "max_cases_exceeded")

    def test_readiness_reports_missing_env(self):
        guard = LiveEvalGuard(required_env=["SOME_FAKE_ENV_123"])
        readiness = guard.readiness()
        self.assertFalse(readiness["ready"])
        self.assertIn("SOME_FAKE_ENV_123", readiness["missing_env"])


if __name__ == "__main__":
    unittest.main()
