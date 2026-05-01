from __future__ import annotations

import pytest
from fastapi import Request
from fastapi import HTTPException

from taos.apps.api.middleware.auth import AuthMiddleware
from taos.apps.api.routes import debug as debug_route


def _request_with_uid(path: str = "/debug/research-cache") -> Request:
    scope = {
        "type": "http",
        "headers": [(b"x-request-id", b"rid-debug")],
        "method": "GET",
        "path": path,
        "query_string": b"",
    }
    req = Request(scope)
    req.state.auth_uid = "u_debug"
    return req


def test_auth_middleware_protects_debug_prefix():
    middleware = AuthMiddleware(app=lambda scope, receive, send: None)
    assert "/debug" in middleware._protected_prefixes


@pytest.mark.asyncio
async def test_route_debug_exposes_owner_and_execution_boundaries():
    out = await debug_route.route_debug(
        debug_route.DebugRouteRequest(query="current vite version")
    )
    assert out["route"] == "fast_search"
    assert out["route_owner"] == "search_lite"
    assert out["will_use_web"] is True
    assert out["will_use_search_lite"] is True
    assert out["will_use_planner"] is False
    assert out["will_use_research_pipeline"] is False
    assert out["llm_fallback_used"] is False
    assert out["normalized_query"] == "current vite version"


@pytest.mark.asyncio
async def test_route_debug_marks_task_as_planner_owned():
    out = await debug_route.route_debug(
        debug_route.DebugRouteRequest(query="run a workflow to send email")
    )
    assert out["route"] == "task"
    assert out["route_owner"] == "fsm_planner"
    assert out["will_use_planner"] is True
    assert out["will_use_web"] is False


@pytest.mark.asyncio
async def test_research_cache_debug_hit_with_query(monkeypatch):
    class FakeSchema:
        def __init__(self):
            self.store = self

        async def retrieve_research_profiles(self, **kwargs):
            return [
                {
                    "id": "rp_1",
                    "query": "openai ceo profile",
                    "retrieval_score": 0.77,
                    "created_at": 1.0,
                    "expires_at": 2.0,
                    "related_questions": ["latest leadership update?"],
                    "agreement": {"agreement_level": "medium"},
                    "source_rows": [{"title": "OpenAI", "link": "https://openai.com"}],
                }
            ]

        async def list(self, *args, **kwargs):  # pragma: no cover - unused in this test
            return []

    monkeypatch.setattr(debug_route, "FirestoreMemorySchema", FakeSchema)

    out = await debug_route.research_cache_debug(
        raw_request=_request_with_uid(),
        query="who is openai ceo",
        user_id="u_debug",
        limit=3,
    )
    assert out["hit"] is True
    assert out["count"] == 1
    assert out["rows"][0]["retrieval_score"] == 0.77


@pytest.mark.asyncio
async def test_research_cache_debug_list_without_query(monkeypatch):
    class FakeSchema:
        def __init__(self):
            self.store = self

        async def retrieve_research_profiles(self, **kwargs):  # pragma: no cover - unused in this test
            return []

        async def list(self, collection, user_id, limit):
            assert collection == "research_profiles"
            return [
                {
                    "id": "rp_2",
                    "query": "nvidia ceo profile",
                    "created_at": 100.0,
                    "expires_at": 999.0,
                    "related_questions": ["linkedin profile?"],
                    "source_rows": [{"title": "NVIDIA", "link": "https://nvidia.com"}],
                }
            ]

    monkeypatch.setattr(debug_route, "FirestoreMemorySchema", FakeSchema)

    out = await debug_route.research_cache_debug(
        raw_request=_request_with_uid(),
        query="",
        user_id="u_debug",
        limit=5,
    )
    assert out["hit"] is False
    assert out["count"] == 1
    assert out["rows"][0]["id"] == "rp_2"


@pytest.mark.asyncio
async def test_research_extractors_debug_returns_candidate_rows(monkeypatch):
    class FakeEngine:
        def __init__(self):
            self._settings = type("S", (), {})()
            self._trace_data = {
                "request_id": "rid_extract_1",
                "route_label": "deep_research",
                "planner_path": "entity_lookup",
                "query_kind": "entity_lookup",
                "verification_state": "not_verified",
                "policy_reason": "candidate_sources_without_explicit_role_confirmation",
                "extractor_adapters": {"web_extract": 2},
                "extractor_adapter_fallback_reasons": ["scrapling_normalized_unsuccessful status=200 text_len=0"],
                "extractor_candidates": [
                    {
                        "rank": 1,
                        "url": "https://example.com/company",
                        "selected_adapter": "scrapling_http_d4vinci",
                        "final_adapter": "web_extract",
                        "fallback_reason": "scrapling_normalized_unsuccessful status=200 text_len=0",
                        "attempted": True,
                        "success": False,
                    }
                ],
                "evidence_stats": {
                    "source_count": 1,
                    "extract_count": 0,
                    "extract_rejected_count": 1,
                    "confirmed_count": 0,
                    "partially_confirmed_count": 0,
                    "not_verified_count": 1,
                    "http_attempts": 1,
                    "dynamic_attempts": 0,
                    "stealth_attempts": 0,
                    "fallback_count": 1,
                },
            }

        def _reset_execution_trace(self, **kwargs):
            self._trace_data["request_id"] = kwargs.get("request_id", "rid_extract_1")

        async def _run_entity_lookup(self, goal: str):
            return f"Answer for {goal}"

    monkeypatch.setattr(debug_route, "OrchestrationEngine", lambda: FakeEngine())

    out = await debug_route.research_extractors_debug(
        raw_request=_request_with_uid("/debug/research-extractors"),
        query="who is the ceo of relyce infotech",
        user_id="u_debug",
    )
    assert str(out["request_id"]).startswith("debug_extract_")
    assert out["query_kind"] == "entity_lookup"
    assert out["candidate_count"] == 1
    assert out["extractor_candidates"][0]["final_adapter"] == "web_extract"
    assert "scrapling_normalized_unsuccessful" in out["extractor_candidates"][0]["fallback_reason"]
    assert out["not_verified_count"] == 1
    assert out["http_attempts"] == 1


@pytest.mark.asyncio
async def test_research_extractors_debug_requires_query():
    with pytest.raises(HTTPException) as exc:
        await debug_route.research_extractors_debug(
            raw_request=_request_with_uid("/debug/research-extractors"),
            query="",
            user_id="u_debug",
        )
    assert exc.value.status_code == 400
