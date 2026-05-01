from __future__ import annotations

import sys
import types
import unittest

from taos.core.routing import RouteCache, RouteDecider

if "httpx" not in sys.modules:
    sys.modules["httpx"] = types.SimpleNamespace(
        AsyncClient=object,
        TimeoutException=Exception,
        HTTPError=Exception,
        ConnectError=Exception,
        ReadTimeout=Exception,
        Response=object,
    )

try:
    from taos.orchestration.engine import OrchestrationEngine
except Exception:
    OrchestrationEngine = None


class FakeFallback:
    async def decide(self, query, context=None):
        return {"route": "task", "confidence": 0.7, "reason": "ambiguous"}


class Phase106RouteBoundariesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cache = RouteCache()
        self.cache.clear()
        self.decider = RouteDecider(cache=self.cache, llm_fallback=FakeFallback())

    async def test_simple_definition_stays_out_of_task_route(self):
        decision = await self.decider.decide("what is docker")
        self.assertEqual(decision.route, "no_search")

    async def test_fast_search_stays_out_of_planner_route(self):
        decision = await self.decider.decide("current vite version")
        self.assertEqual(decision.route, "fast_search")

    async def test_document_question_routes_doc_mode(self):
        decision = await self.decider.decide("explain this pdf", context={"has_active_doc": True})
        self.assertEqual(decision.route, "doc_mode")


@unittest.skipIf(OrchestrationEngine is None, "engine dependencies unavailable in lightweight runtime")
class Phase106EngineOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = OrchestrationEngine()
        self.classification = types.SimpleNamespace(
            intent="simple_lookup",
            domain="general",
            metadata={},
        )

        async def fake_finalize(*, raw_result, **kwargs):
            return {
                "raw_result": raw_result,
                "planner_path": self.engine._trace_data.get("planner_path"),
                "dag_name": self.engine._trace_data.get("dag_name"),
                "route_label": self.engine._trace_data.get("route_label"),
            }

        self.engine._finalize = fake_finalize  # type: ignore[method-assign]

    async def test_search_owner_uses_search_lite_without_planner(self):
        async def fake_run_fast_search(goal: str):
            return f"fast:{goal}"

        self.engine._run_fast_search = fake_run_fast_search  # type: ignore[method-assign]
        result = await self.engine._execute_route_owner_path(
            route_owner="search_lite",
            goal="current vite version",
            effective_goal="Research and verify with current sources: current vite version",
            classification=self.classification,
            user_id="u1",
            tracker=None,
            doc_context_active=False,
            doc_ids=[],
        )
        self.assertEqual(result["planner_path"], "fast_search")
        self.assertEqual(result["dag_name"], "search_lite")
        self.assertEqual(result["raw_result"], "fast:current vite version")

    async def test_search_owner_escalates_unverified_version_lookup_to_research(self):
        async def fake_run_fast_search(goal: str):
            self.engine._trace_data["evidence_stats"] = {
                "query_kind": "version_lookup",
                "verification_state": "not_verified",
                "official_source_required": True,
                "fast_search_escalation_recommended": True,
            }
            self.engine._trace_data["verification_state"] = "not_verified"
            return f"fast:{goal}"

        async def fake_run_deep_research(goal: str):
            return f"research:{goal}"

        self.engine._run_fast_search = fake_run_fast_search  # type: ignore[method-assign]
        self.engine._run_deep_research = fake_run_deep_research  # type: ignore[method-assign]
        result = await self.engine._execute_route_owner_path(
            route_owner="search_lite",
            goal="current vite version",
            effective_goal="current vite version",
            classification=self.classification,
            user_id="u1",
            tracker=None,
            doc_context_active=False,
            doc_ids=[],
        )
        self.assertEqual(result["planner_path"], "deep_research")
        self.assertEqual(result["dag_name"], "research_v2")
        self.assertEqual(result["raw_result"], "research:current vite version")

    async def test_research_owner_uses_research_pipeline_without_planner(self):
        async def fake_run_deep_research(goal: str):
            return f"research:{goal}"

        self.engine._run_deep_research = fake_run_deep_research  # type: ignore[method-assign]
        result = await self.engine._execute_route_owner_path(
            route_owner="research_pipeline",
            goal="research AI jobs India 2026",
            effective_goal="research AI jobs India 2026",
            classification=self.classification,
            user_id="u1",
            tracker=None,
            doc_context_active=False,
            doc_ids=[],
        )
        self.assertEqual(result["planner_path"], "deep_research")
        self.assertEqual(result["dag_name"], "research_v2")
        self.assertEqual(result["raw_result"], "research:research AI jobs India 2026")

    async def test_research_owner_package_version_uses_source_of_record_fast_search_first(self):
        async def fake_run_fast_search(goal: str):
            self.engine._trace_data["evidence_stats"] = {
                "query_kind": "version_lookup",
                "verification_state": "verified",
                "official_source_required": True,
                "official_source_found": True,
                "fast_search_escalation_recommended": False,
            }
            self.engine._trace_data["verification_state"] = "verified"
            return f"fast:{goal}"

        async def fake_run_deep_research(goal: str):
            raise AssertionError("deep research should not run when registry lookup verifies package version")

        self.engine._run_fast_search = fake_run_fast_search  # type: ignore[method-assign]
        self.engine._run_deep_research = fake_run_deep_research  # type: ignore[method-assign]
        result = await self.engine._execute_route_owner_path(
            route_owner="research_pipeline",
            goal="deep research current vite version",
            effective_goal="Research and verify with current sources: deep research current vite version",
            classification=self.classification,
            user_id="u1",
            tracker=None,
            doc_context_active=False,
            doc_ids=[],
        )
        self.assertEqual(result["planner_path"], "fast_search")
        self.assertEqual(result["dag_name"], "search_lite")
        self.assertEqual(result["route_label"], "fast_search")
        self.assertEqual(result["raw_result"], "fast:deep research current vite version")

    async def test_run_preempts_package_version_before_semantic_research_classification(self):
        async def fake_run_fast_search(goal: str):
            self.engine._trace_data["evidence_stats"] = {
                "query_kind": "version_lookup",
                "verification_state": "verified",
                "source_type": "package_registry",
                "source_domain": "npmjs.com",
                "generic_web_used": False,
                "official_source_required": True,
                "official_source_found": True,
                "fast_search_escalation_recommended": False,
            }
            self.engine._trace_data["verification_state"] = "verified"
            return f"fast:{goal}"

        def fail_classify(*args, **kwargs):
            raise AssertionError("semantic classifier should not run for early source-of-record preempt")

        def fail_rewrite(*args, **kwargs):
            raise AssertionError("query rewrite should not run for early source-of-record preempt")

        async def fail_route_owner(*args, **kwargs):
            raise AssertionError("route-owner deep/search dispatch should not run after early preempt")

        async def fail_deep_research(goal: str):
            raise AssertionError("deep research should not run after early source-of-record preempt")

        async def fake_finalize(*, raw_result, classification, **kwargs):
            return {
                "raw_result": raw_result,
                "intent": classification.intent.value,
                "route_label": self.engine._trace_data.get("route_label"),
                "planner_path": self.engine._trace_data.get("planner_path"),
                "dag_name": self.engine._trace_data.get("dag_name"),
                "route_decision": self.engine._trace_data.get("route_decision"),
                "route_boundary_summary": self.engine._trace_data.get("route_boundary_summary"),
                "planning_handoff": self.engine._trace_data.get("planning_handoff"),
            }

        self.engine._run_fast_search = fake_run_fast_search  # type: ignore[method-assign]
        self.engine._run_deep_research = fail_deep_research  # type: ignore[method-assign]
        self.engine._execute_route_owner_path = fail_route_owner  # type: ignore[method-assign]
        self.engine._intent_classifier.classify = fail_classify  # type: ignore[method-assign]
        self.engine._query_rewriter.rewrite = fail_rewrite  # type: ignore[method-assign]
        self.engine._compute_request_deadline_seconds = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("research time budget should not be computed for early preempt")
        )
        self.engine._finalize = fake_finalize  # type: ignore[method-assign]

        result = await self.engine.run(
            "deep research current vite version",
            request_id="phase108b",
            user_id="u1",
            include_trace=True,
        )

        self.assertEqual(result["raw_result"], "fast:deep research current vite version")
        self.assertEqual(result["intent"], "simple_lookup")
        self.assertEqual(result["route_label"], "fast_search")
        self.assertEqual(result["planner_path"], "fast_search")
        self.assertEqual(result["dag_name"], "search_lite")
        self.assertEqual(result["route_decision"]["route"], "fast_search")
        self.assertEqual(result["route_decision"]["route_owner"], "search_lite")
        self.assertEqual(result["route_boundary_summary"]["owner"], "search_lite")
        self.assertEqual(result["planning_handoff"]["rewritten_query"], "deep research current vite version")

    async def test_fsm_owner_returns_none_for_planner_handoff(self):
        result = await self.engine._execute_route_owner_path(
            route_owner="fsm_planner",
            goal="fix this code error",
            effective_goal="fix this code error",
            classification=self.classification,
            user_id="u1",
            tracker=None,
            doc_context_active=False,
            doc_ids=[],
        )
        self.assertIsNone(result)

    async def test_document_owner_emits_layered_cache_summary_for_doc_ask(self):
        async def fake_doc_answer(**kwargs):
            return (
                "doc:answer",
                [{"link": "internal://doc/doc_1", "provider": "document"}],
                "doc_mode_retrieval",
                {
                    "metadata": {"cache_hit": True},
                    "warnings": [],
                },
            )

        self.engine._resolve_doc_mode_direct_answer = fake_doc_answer  # type: ignore[method-assign]
        self.engine._build_doc_mode_summary = lambda **kwargs: {  # type: ignore[method-assign]
            "retrieval_strength": "strong",
            "validation_score": 0.88,
            "validation_issue_count": 0,
        }
        result = await self.engine._execute_route_owner_path(
            route_owner="document_pipeline",
            goal="summarize this pdf",
            effective_goal="summarize this pdf",
            classification=self.classification,
            user_id="u1",
            tracker=None,
            doc_context_active=True,
            doc_ids=["doc_1"],
        )
        self.assertEqual(result["planner_path"], "doc_mode_retrieval")
        cache_summary = self.engine._trace_data.get("evidence_stats", {}).get("cache_summary", {})
        self.assertEqual(cache_summary["document_ask"]["hit"], 1)
        self.assertEqual(cache_summary["document_ask"]["miss"], 0)
        self.assertTrue(cache_summary["doc_ask_cache_hit"])


if __name__ == "__main__":
    unittest.main()
