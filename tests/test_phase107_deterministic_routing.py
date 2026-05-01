from __future__ import annotations

import asyncio
import unittest

from taos.apps.api.response_contract import normalize_contract_payload
from taos.core.routing import RouteCache, RouteDecider


class FakeFallback:
    def __init__(self, payload=None, delay: float = 0.0) -> None:
        self.payload = payload
        self.delay = delay
        self.calls = 0

    async def decide(self, query, context=None):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.payload


class Phase107DeterministicRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cache = RouteCache()
        self.cache.clear()
        self.fallback = FakeFallback()
        self.decider = RouteDecider(cache=self.cache, llm_fallback=self.fallback)

    async def test_hi_routes_fast_message_without_llm(self):
        decision = await self.decider.decide("hi")
        self.assertEqual(decision.route, "fast_message")
        self.assertFalse(decision.used_llm)
        self.assertEqual(self.fallback.calls, 0)

    async def test_what_is_docker_routes_no_search_without_llm(self):
        decision = await self.decider.decide("what is docker")
        self.assertEqual(decision.route, "no_search")
        self.assertFalse(decision.used_llm)
        self.assertIn("universal_understanding", decision.matched_rules)

    async def test_tanglish_definition_routes_no_search_without_llm(self):
        decision = await self.decider.decide("docker na enna")
        self.assertEqual(decision.route, "no_search")
        self.assertFalse(decision.used_llm)

    async def test_current_vite_version_routes_fast_search(self):
        decision = await self.decider.decide("current vite version")
        self.assertEqual(decision.route, "fast_search")
        self.assertFalse(decision.used_llm)

    async def test_current_ceo_routes_entity_lookup(self):
        decision = await self.decider.decide("who is the current ceo of openai")
        self.assertEqual(decision.route, "entity_lookup")
        self.assertFalse(decision.used_llm)

    async def test_breakup_support_query_routes_no_search(self):
        decision = await self.decider.decide("fina i got an break up today")
        self.assertEqual(decision.route, "no_search")
        self.assertFalse(decision.used_llm)

    async def test_latest_openai_news_today_routes_news_search(self):
        decision = await self.decider.decide("latest OpenAI news today")
        self.assertEqual(decision.route, "news_search")

    async def test_research_ai_job_market_india_2026_routes_deep_search(self):
        decision = await self.decider.decide("research AI job market India 2026")
        self.assertEqual(decision.route, "deep_search")

    async def test_rag_vs_fine_tuning_routes_comparison_search(self):
        decision = await self.decider.decide("RAG vs fine tuning")
        self.assertEqual(decision.route, "comparison_search")

    async def test_active_document_question_routes_doc_mode(self):
        decision = await self.decider.decide(
            "explain this pdf",
            context={"has_active_doc": True},
        )
        self.assertEqual(decision.route, "doc_mode")

    async def test_ambiguous_query_prefers_clarification_without_llm(self):
        fallback = FakeFallback({"route": "task", "confidence": 0.72, "reason": "ambiguous_action"})
        decider = RouteDecider(cache=self.cache, llm_fallback=fallback)
        decision = await decider.decide("do it")
        self.assertEqual(decision.route, "clarification")
        self.assertFalse(decision.used_llm)
        self.assertEqual(fallback.calls, 0)

    async def test_low_signal_prompt_does_not_need_llm_timeout_path(self):
        fallback = FakeFallback({"route": "task", "confidence": 0.99, "reason": "late"}, delay=2.0)
        decider = RouteDecider(cache=self.cache, llm_fallback=fallback, llm_timeout_seconds=0.05)
        decision = await decider.decide("do it")
        self.assertEqual(decision.boundary, "universal_understanding")
        self.assertFalse(decision.used_llm)
        self.assertEqual(decision.route, "clarification")

    async def test_route_cache_hit_avoids_routing_work(self):
        first = await self.decider.decide("what is docker")
        self.assertEqual(first.cache_status, "miss")
        second = await self.decider.decide("what is docker?")
        self.assertEqual(second.route, "no_search")
        self.assertEqual(second.cache_status, "hit")
        self.assertFalse(second.used_llm)

    def test_normal_and_stream_payloads_preserve_route_decision_metadata(self):
        route_decision = {
            "route": "fast_search",
            "confidence": 0.84,
            "used_llm": False,
            "cache_status": "miss",
            "boundary": "deterministic",
        }
        boundary = {
            "route": "fast_search",
            "owner": "search_lite",
            "planner_allowed": False,
            "web_search_allowed": True,
        }
        payload = normalize_contract_payload(
            {
                "answer": "Latest version answer",
                "direct_answer": "Latest version answer",
                "key_points": [],
                "sections": [],
                "confidence": 0.84,
                "trust_block": None,
                "sources": [],
                "route": "fast_search",
                "mode": "fast_search",
                "warnings": [],
                "route_decision": route_decision,
                "route_boundary_summary": boundary,
            }
        )
        self.assertEqual(payload["metadata"]["route_decision"]["route"], "fast_search")
        self.assertFalse(payload["metadata"]["route_boundary_summary"]["planner_allowed"])


if __name__ == "__main__":
    unittest.main()
