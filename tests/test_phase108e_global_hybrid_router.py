from __future__ import annotations

import unittest

from taos.core.routing import GlobalHybridRouter, RouteCache, RouteDecider


class FakeFallback:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, query, context=None):
        self.calls += 1
        return {"route": "task", "confidence": 0.7, "reason": "fallback"}


class Phase108EGlobalHybridRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cache = RouteCache()
        self.cache.clear()
        self.fallback = FakeFallback()
        self.decider = RouteDecider(cache=self.cache, llm_fallback=self.fallback)

    async def test_deterministic_package_fast_path_stays_fast(self):
        decision = await self.decider.decide("current vite version")
        self.assertEqual(decision.route, "fast_search")
        self.assertEqual(decision.boundary, "deterministic")
        self.assertFalse(decision.used_llm)

    async def test_multilingual_french_package_lookup_routes_registry_first(self):
        decision = await self.decider.decide("Quelle est la derniere version de Vite?")
        self.assertEqual(decision.route, "fast_search")
        self.assertEqual(decision.boundary, "global_hybrid")
        self.assertEqual(decision.routing_signals["normalized_intent"], "package_lookup")
        self.assertIn("npm_registry_lookup", decision.routing_signals["preferred_tools"])
        self.assertIn("vite", [item.lower() for item in decision.routing_signals["entities"]])
        self.assertFalse(decision.used_llm)

    async def test_multilingual_cjk_package_lookup_routes_registry_first(self):
        decision = await self.decider.decide("React 的最新版本是多少？")
        self.assertEqual(decision.route, "fast_search")
        self.assertEqual(decision.routing_signals["normalized_intent"], "package_lookup")
        self.assertEqual(decision.routing_signals["source_policy"], "registry_first")

    async def test_transliterated_package_lookup_routes_registry_first(self):
        decision = await self.decider.decide("Vite ka latest version kya hai?")
        self.assertEqual(decision.route, "fast_search")
        self.assertEqual(decision.routing_signals["normalized_intent"], "package_lookup")
        self.assertEqual(decision.routing_signals["entities"][0], "vite")

    async def test_official_pricing_query_uses_official_source_policy(self):
        decision = await self.decider.decide("official Stripe pricing")
        self.assertEqual(decision.route, "official_search")
        self.assertEqual(decision.boundary, "global_hybrid")
        self.assertEqual(decision.routing_signals["normalized_intent"], "official_source_lookup")
        self.assertEqual(decision.routing_signals["source_policy"], "official_first")
        self.assertIn("official_web_search", decision.routing_signals["preferred_tools"])

    async def test_complex_comparison_uses_tool_planner_signals(self):
        decision = await self.decider.decide("compare Supabase vs Firebase for production SaaS in 2026")
        self.assertEqual(decision.route, "comparison_search")
        self.assertEqual(decision.routing_signals["normalized_intent"], "comparison_research")
        self.assertEqual(decision.routing_signals["source_policy"], "official_plus_diverse_independent")
        self.assertIn("web_extract", decision.routing_signals["preferred_tools"])

    async def test_general_explanation_avoids_llm_router(self):
        decision = await self.decider.decide("Explain reinforcement learning in simple terms")
        self.assertEqual(decision.route, "no_search")
        self.assertEqual(decision.boundary, "deterministic")
        self.assertFalse(decision.used_llm)
        self.assertEqual(self.fallback.calls, 0)

    def test_router_direct_signal_shape(self):
        router = GlobalHybridRouter()
        decision = router.route("is this medicine safe with my condition?")
        self.assertEqual(decision.route, "official_search")
        self.assertTrue(decision.high_stakes)
        self.assertEqual(decision.signals["normalized_intent"], "high_stakes_research")
        self.assertEqual(decision.signals["source_policy"], "official_first")


if __name__ == "__main__":
    unittest.main()
