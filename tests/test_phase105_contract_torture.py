from __future__ import annotations

import unittest

from taos.apps.api.response_contract import build_clarification_payload, build_timeout_payload, normalize_contract_payload


class Phase105ContractTortureTests(unittest.TestCase):
    def test_timeout_payload_keeps_unified_contract(self):
        payload = normalize_contract_payload(build_timeout_payload(request_id="req1", elapsed_ms=12.0, partial_result="partial"))
        for key in ("answer", "direct_answer", "key_points", "sections", "confidence", "trust_block", "sources", "route", "warnings", "metadata"):
            self.assertIn(key, payload)

    def test_clarification_payload_keeps_unified_contract(self):
        payload = normalize_contract_payload(build_clarification_payload(request_id="req2", query="this one"))
        self.assertEqual(payload["metadata"]["fallback_reason"], "clarification")
        self.assertEqual(payload["route"], "standard_task")

    def test_research_optional_summaries_are_preserved_in_metadata(self):
        payload = normalize_contract_payload(
            {
                "answer": "Research answer",
                "direct_answer": "Research answer",
                "key_points": [],
                "sections": [],
                "confidence": 0.6,
                "trust_block": {},
                "sources": [],
                "route": "deep_research",
                "mode": "deep",
                "freshness_summary": {"freshness_score": 0.8},
                "evidence_selection_summary": {"selected_count": 3},
                "citation_plan_summary": {"unsupported_factual_paragraphs": 1},
                "diversity_summary": {"unique_domains": 3},
                "conflict_summary": {"unresolved_conflict_count": 1},
                "high_stakes_summary": {"high_stakes": True},
                "cache_summary": {"search": {"hit": 1, "miss": 1, "stale": 0}},
            }
        )
        self.assertIn("freshness_summary", payload["metadata"])
        self.assertIn("conflict_summary", payload["metadata"])
        self.assertIn("cache_summary", payload["metadata"])


if __name__ == "__main__":
    unittest.main()
