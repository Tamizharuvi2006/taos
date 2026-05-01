from __future__ import annotations

import asyncio

import pytest

from taos.orchestration.engine import OrchestrationEngine


@pytest.mark.asyncio
async def test_deep_research_no_evidence_returns_controlled_unverified_message(monkeypatch):
    async def _fake_web_search(*args, **kwargs):
        return {"success": True, "results": []}

    async def _fake_web_extract(*args, **kwargs):
        return {"success": False}

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)

    engine = OrchestrationEngine()
    out = await engine._run_deep_research("research about iran and israel war current status")

    assert out is not None
    lowered = out.lower()
    assert "couldn't verify this confidently" in lowered
    assert "what was searched" in lowered
    assert "what was not found" in lowered
    assert "next useful moves" in lowered
    assert "[s1]" in lowered
    assert "sources" in lowered
    assert "as of my last update" not in lowered


@pytest.mark.asyncio
async def test_deep_research_no_evidence_marks_trace_fallback(monkeypatch):
    async def _fake_web_search(*args, **kwargs):
        return {"success": True, "results": []}

    async def _fake_web_extract(*args, **kwargs):
        return {"success": False}

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)

    engine = OrchestrationEngine()
    engine._reset_execution_trace(
        request_id="req_no_evidence",
        goal="research about iran and israel war current status",
        include_trace=True,
    )
    _ = await engine._run_deep_research("research about iran and israel war current status")

    assert engine._trace_data["fallback_used"] is True
    assert engine._trace_data["fallback_reason"] == "research_unverified_fallback"
    freshness = engine._trace_data.get("freshness_check") or {}
    assert freshness.get("status") == "failed"


@pytest.mark.asyncio
async def test_deep_research_provider_error_marks_web_search_failed(monkeypatch):
    async def _fake_web_search(*args, **kwargs):
        return {"error": "web_search_failed: RuntimeError", "results": []}

    async def _fake_web_extract(*args, **kwargs):
        return {"success": False}

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)

    engine = OrchestrationEngine()
    engine._reset_execution_trace(
        request_id="req_provider_down",
        goal="research about iran and israel war current status",
        include_trace=True,
    )
    _ = await engine._run_deep_research("research about iran and israel war current status")

    assert engine._trace_data["fallback_used"] is True
    assert engine._trace_data["fallback_reason"] == "web_search_failed"
    freshness = engine._trace_data.get("freshness_check") or {}
    assert freshness.get("status") == "failed"
    assert "Search provider returned errors" in str(freshness.get("note") or "")


@pytest.mark.asyncio
async def test_deep_research_rejects_stale_synthesis_and_emits_evidence_fallback(monkeypatch):
    async def _fake_web_search(*args, **kwargs):
        return {
            "success": True,
            "results": [
                {
                    "title": "Live source",
                    "link": "https://example.com/live",
                    "snippet": "Update from April 7, 2026 with current-status details.",
                }
            ],
        }

    async def _fake_web_extract(*args, **kwargs):
        return {
            "success": True,
            "url": kwargs.get("url", ""),
            "title": "Live source",
            "published_at": "2026-04-07T10:00:00Z",
            "text": "Confirmed event timeline and source-backed context.",
            "usable_for_research": True,
            "quality_score": 0.88,
        }

    async def _fake_synthesize(*args, **kwargs):
        return "As of my last update in October 2023, I cannot verify this."

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)

    engine = OrchestrationEngine()
    monkeypatch.setattr(engine, "_synthesize_research", _fake_synthesize)

    out = await engine._run_deep_research("research about iran and israel war current status")

    assert out is not None
    lowered = out.lower()
    assert "as of my last update" not in lowered
    assert "timeline (newest evidence first)" in lowered
    assert "sources:" in lowered
    assert "[s1]" in lowered


@pytest.mark.asyncio
async def test_deep_research_sparse_ranked_rows_returns_structured_unverified_message(monkeypatch):
    async def _fake_web_search(*args, **kwargs):
        return {
            "success": True,
            "results": [
                {
                    "title": "Candidate source",
                    "link": "https://example.com/article",
                    "snippet": "Some candidate text.",
                }
            ],
        }

    async def _fake_web_extract(*args, **kwargs):
        return {"success": False}

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)

    engine = OrchestrationEngine()
    monkeypatch.setattr(engine, "_rank_research_evidence", lambda *args, **kwargs: [])
    engine._reset_execution_trace(
        request_id="req_sparse",
        goal="latest update on topic x",
        include_trace=True,
    )
    out = await engine._run_deep_research("latest update on topic x")

    assert out is not None
    lowered = out.lower()
    assert "couldn't verify this confidently" in lowered
    assert "none passed ranking/diversity quality checks" in lowered
    assert engine._trace_data["fallback_used"] is True
    assert engine._trace_data["fallback_reason"] == "search_sparse"


@pytest.mark.asyncio
async def test_deep_research_extract_failed_returns_structured_unverified_message(monkeypatch):
    async def _fake_web_search(*args, **kwargs):
        return {
            "success": True,
            "results": [
                {
                    "title": "Candidate source",
                    "link": "https://example.com/article",
                    "snippet": "Recent report on topic y.",
                }
            ],
        }

    async def _fake_web_extract(*args, **kwargs):
        return {"success": False}

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)

    engine = OrchestrationEngine()
    async def _no_cache(*args, **kwargs):
        return None
    monkeypatch.setattr(engine, "_lookup_research_profile_cache", _no_cache)
    engine._reset_execution_trace(
        request_id="req_extract_fail",
        goal="latest topic y status",
        include_trace=True,
    )
    out = await engine._run_deep_research("latest topic y status")

    assert out is not None
    lowered = out.lower()
    assert "key evidence" in lowered
    assert "couldn't verify this confidently" not in lowered
    assert engine._trace_data["fallback_used"] is True
    assert engine._trace_data["fallback_reason"] in {"research_evidence_fallback", "research_timeout"}
    freshness = engine._trace_data.get("freshness_check") or {}
    assert freshness.get("status") in {"recovered", "failed"}


@pytest.mark.asyncio
async def test_deep_research_search_stage_timeout_returns_timeout_fallback(monkeypatch):
    async def _slow_web_search(*args, **kwargs):
        await asyncio.sleep(0.08)
        return {"success": True, "results": []}

    async def _fake_web_extract(*args, **kwargs):
        return {"success": False}

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _slow_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)

    engine = OrchestrationEngine()
    engine._SEARCH_STAGE_TIMEOUT_SECONDS = 0.01
    engine._reset_execution_trace(
        request_id="req_search_timeout",
        goal="latest update on topic timeout case",
        include_trace=True,
    )
    out = await engine._run_deep_research("latest update on topic timeout case")

    assert out is not None
    lowered = out.lower()
    assert "couldn't verify this confidently" in lowered
    assert engine._trace_data["fallback_used"] is True
    assert engine._trace_data["fallback_reason"] in {
        "research_timeout",
        "web_search_failed",
        "research_unverified_fallback",
    }


@pytest.mark.asyncio
async def test_deep_research_zero_primary_results_recovers_with_targeted_queries(monkeypatch):
    async def _fake_web_search(*args, **kwargs):
        q = str(kwargs.get("query") or "").lower()
        if "openai ceo" in q or "linkedin profile" in q:
            return {
                "success": True,
                "results": [
                    {
                        "title": "OpenAI Leadership",
                        "link": "https://openai.com/about/",
                        "snippet": "OpenAI leadership includes CEO Sam Altman.",
                    }
                ],
            }
        return {"success": True, "results": []}

    async def _fake_web_extract(*args, **kwargs):
        return {
            "success": True,
            "url": kwargs.get("url", ""),
            "title": "OpenAI Leadership",
            "published_at": "2026-04-10T00:00:00Z",
            "text": "OpenAI leadership page names Sam Altman as CEO.",
            "usable_for_research": True,
            "quality_score": 0.92,
        }

    async def _fake_generate_research_queries(_goal):
        return ["research about open ai ce"]

    async def _fake_synthesize(*args, **kwargs):
        return (
            "Answer\n"
            "OpenAI's CEO is Sam Altman.\n\n"
            "Sources\n"
            "[S1] OpenAI Leadership - https://openai.com/about/"
        )

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)

    engine = OrchestrationEngine()
    monkeypatch.setattr(engine, "_generate_research_queries", _fake_generate_research_queries)
    monkeypatch.setattr(engine, "_synthesize_research", _fake_synthesize)
    engine._reset_execution_trace(
        request_id="req_recovery_search",
        goal="research about open ai ce",
        include_trace=True,
    )
    out = await engine._run_deep_research("research about open ai ce")

    assert out is not None
    lowered = out.lower()
    assert "sam altman" in lowered
    assert "couldn't verify this confidently" not in lowered
    steps = engine._trace_data.get("steps") or []
    assert any("recovered" in str(step.get("summary", "")).lower() for step in steps)


@pytest.mark.asyncio
async def test_deep_research_returns_synthesized_output_when_available(monkeypatch):
    async def _fake_web_search(*args, **kwargs):
        return {
            "success": True,
            "results": [
                {
                    "title": "OpenAI Leadership",
                    "link": "https://openai.com/about/",
                    "snippet": "OpenAI leadership includes CEO Sam Altman.",
                }
            ],
        }

    async def _fake_web_extract(*args, **kwargs):
        return {
            "success": True,
            "url": kwargs.get("url", ""),
            "title": "OpenAI Leadership",
            "published_at": "2026-04-10T00:00:00Z",
            "text": "OpenAI leadership page names Sam Altman as CEO.",
            "usable_for_research": True,
            "quality_score": 0.91,
        }

    async def _fake_synthesize(*args, **kwargs):
        return (
            "Answer\n"
            "OpenAI's CEO is Sam Altman.\n\n"
            "Sources\n"
            "[S1] OpenAI Leadership - https://openai.com/about/"
        )

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)

    engine = OrchestrationEngine()
    async def _no_cache(*args, **kwargs):
        return None
    monkeypatch.setattr(engine, "_lookup_research_profile_cache", _no_cache)
    monkeypatch.setattr(engine, "_synthesize_research", _fake_synthesize)
    engine._reset_execution_trace(
        request_id="req_synth_success",
        goal="research about open ai ceo",
        include_trace=True,
    )
    out = await engine._run_deep_research("research about open ai ceo")

    assert out is not None
    lowered = out.lower()
    assert "openai's ceo is sam altman" in lowered
    assert "couldn't verify this confidently" not in lowered
    steps = engine._trace_data.get("steps") or []
    assert any(
        "synthesized final answer" in str(step.get("summary", "")).lower()
        for step in steps
    )


@pytest.mark.asyncio
async def test_deep_research_extract_failed_uses_evidence_fallback_for_profile_query(monkeypatch):
    async def _fake_web_search(*args, **kwargs):
        return {
            "success": True,
            "results": [
                {
                    "title": "OpenAI leadership",
                    "link": "https://openai.com/about/",
                    "snippet": "OpenAI leadership page lists Sam Altman as CEO.",
                },
                {
                    "title": "Sam Altman biography",
                    "link": "https://www.britannica.com/biography/Sam-Altman",
                    "snippet": "Sam Altman is an American entrepreneur and CEO of OpenAI.",
                },
                {
                    "title": "LinkedIn profile",
                    "link": "https://www.linkedin.com/in/samaltman/",
                    "snippet": "Sam Altman profile and role references.",
                },
            ],
        }

    async def _fake_web_extract(*args, **kwargs):
        return {"success": False, "error": "blocked"}

    monkeypatch.setattr("taos.core.tools.builtin.web_search.web_search", _fake_web_search)
    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)

    engine = OrchestrationEngine()
    async def _no_cache(*args, **kwargs):
        return None
    monkeypatch.setattr(engine, "_lookup_research_profile_cache", _no_cache)
    engine._reset_execution_trace(
        request_id="req_extract_profile_fallback",
        goal="research about open ai ceo",
        include_trace=True,
    )
    out = await engine._run_deep_research("research about open ai ceo")

    assert out is not None
    lowered = out.lower()
    assert "couldn't verify this confidently" not in lowered
    assert "key evidence" in lowered
    assert "sources:" in lowered
    assert "[s1]" in lowered


@pytest.mark.asyncio
async def test_profile_query_generation_uses_deterministic_entity_set():
    engine = OrchestrationEngine()
    queries = await engine._generate_research_queries("comprehensive research about open ai ceo")
    normalized = [str(q).strip().lower() for q in queries if str(q).strip()]

    assert len(normalized) >= 4
    assert any("openai ceo" in row for row in normalized)
    assert any("linkedin" in row for row in normalized)
    assert any("site:openai.com leadership team" in row for row in normalized)
    assert all("2023" not in row for row in normalized)


def test_sanitize_research_goal_normalizes_common_typos():
    engine = OrchestrationEngine()
    out = engine._sanitize_research_goal("vresearch about open ai ce")
    assert "vresearch" not in out.lower()
    assert "openai ceo" in out.lower()


def test_extract_profile_query_focus_strips_conversational_prefix():
    engine = OrchestrationEngine()
    focus = engine._extract_profile_query_focus("can u tell me who is the ceo of relyce infotech")
    lowered = focus.lower()
    assert "can u tell me" not in lowered
    assert "who is" not in lowered
    assert "relyce infotech" in lowered


def test_extract_profile_role_and_entity_strips_conversational_noise():
    engine = OrchestrationEngine()
    role, entity = engine._extract_profile_role_and_entity("can u tell me who is the ceo of relyce infotech")
    assert role == "ceo"
    lowered = entity.lower()
    assert "can u tell me" not in lowered
    assert "who is" not in lowered
    assert "relyce infotech" in lowered


def test_profile_evidence_fallback_avoids_event_level_language():
    engine = OrchestrationEngine()
    fallback = engine._build_research_evidence_fallback(
        "can u tell me who is the ceo of relyce infotech",
        evidence_rows=[
            {
                "title": "Relyce infotech - LinkedIn",
                "link": "https://in.linkedin.com/company/relyce-infotech",
                "snippet": "Relyce infotech company profile.",
                "provider": "in.linkedin.com",
            },
            {
                "title": "Unrelated profile",
                "link": "https://example.com/other",
                "snippet": "Another profile page.",
                "provider": "example.com",
            },
        ],
        freshness_mode=False,
        agreement={"agreement_level": "low", "signal": "partial_conflict"},
        high_stakes_mode=False,
    )
    assert fallback is not None
    lowered = fallback.lower()
    assert "event-level" not in lowered
    assert "cannot confidently confirm the current ceo" in lowered


@pytest.mark.asyncio
async def test_profile_cache_lookup_skips_old_event_style_answers(monkeypatch):
    engine = OrchestrationEngine()

    async def _fake_retrieve(*args, **kwargs):
        return [
            {
                "answer": (
                    "As of 2026-04-18, this update is grounded in event-level evidence.\n"
                    "The event is corroborated."
                ),
                "agreement": {"official_source_found": False},
            }
        ]

    monkeypatch.setattr(engine._firestore_memory, "retrieve_research_profiles", _fake_retrieve)
    out = await engine._lookup_research_profile_cache(
        goal="can u tell me who is the ceo of relyce infotech",
        high_stakes_mode=False,
        official_source_required=False,
    )
    assert out is None
