from __future__ import annotations

import pytest

from taos.core.fast_path.fast_path import FastPathResult
from taos.core.semantic.intent_classifier import IntentType
from taos.core.semantic.intent_classifier import ClassificationResult, DomainType
from taos.core.semantic.tone_profile import ToneResult
from taos.orchestration.engine import OrchestrationEngine


def test_enforce_authority_quality_adds_citations_and_followups_for_deep_research():
    engine = OrchestrationEngine()
    text = (
        "Answer\n"
        "A major update was reported today.\n\n"
        "Evidence\n"
        "- Source reported a timeline shift.\n"
    )
    out = engine._enforce_authority_quality_blocks(
        text=text,
        route_label="deep_research",
        intent=IntentType.NEWS,
        planner_path="deep_research",
        source_links=["https://example.com/a", "https://example.com/b"],
        signal="clean",
        stale_detected=False,
        conflict_detected=False,
        high_stakes_mode=False,
    )
    assert "[S1]" in out
    assert "Next useful follow-ups" in out
    assert out.count("\n- ") >= 3


def test_enforce_authority_quality_cites_key_points_bullets():
    engine = OrchestrationEngine()
    text = (
        "Answer\n"
        "Policy update summary.\n\n"
        "Key points\n"
        "- Monetary policy stance unchanged.\n"
        "- Liquidity guidance tightened.\n"
    )
    out = engine._enforce_authority_quality_blocks(
        text=text,
        route_label="deep_research",
        intent=IntentType.NEWS,
        planner_path="deep_research",
        source_links=["https://example.com/a", "https://example.com/b"],
        signal="clean",
        stale_detected=False,
        conflict_detected=False,
        high_stakes_mode=False,
    )
    assert "- Monetary policy stance unchanged. [S" in out
    assert "- Liquidity guidance tightened. [S" in out


def test_enforce_authority_quality_adds_standard_task_followups():
    engine = OrchestrationEngine()
    text = "Here is a direct implementation plan."
    out = engine._enforce_authority_quality_blocks(
        text=text,
        route_label="standard_task",
        intent=IntentType.TASK,
        planner_path="fsm",
        source_links=[],
        signal="clean",
        stale_detected=False,
        conflict_detected=False,
        high_stakes_mode=False,
    )
    assert "Next useful follow-ups" in out
    assert "deeper version" in out.lower()


def test_enforce_authority_quality_adds_citations_and_followups_for_fast_search():
    engine = OrchestrationEngine()
    text = (
        "Answer\n"
        "The latest Vite release is available now.\n\n"
        "Key points\n"
        "- The version line was updated recently.\n"
    )
    out = engine._enforce_authority_quality_blocks(
        text=text,
        route_label="fast_search",
        intent=IntentType.SIMPLE_LOOKUP,
        planner_path="fast_search",
        source_links=["https://vite.dev/blog/vite-release"],
        signal="clean",
        stale_detected=False,
        conflict_detected=False,
        high_stakes_mode=False,
    )
    assert "[S1]" in out
    assert "Next useful follow-ups" in out
    assert "official or primary source" in out.lower()


def test_enforce_authority_quality_adds_citations_for_standard_task_when_sources_exist():
    engine = OrchestrationEngine()
    text = (
        "Answer\n"
        "Python 3.12 introduced notable performance changes.\n\n"
        "Key points\n"
        "- Startup path was improved.\n"
        "- Error suggestions were refined.\n"
    )
    out = engine._enforce_authority_quality_blocks(
        text=text,
        route_label="standard_task",
        intent=IntentType.TASK,
        planner_path="fsm",
        source_links=["https://example.com/python312-release-notes"],
        signal="clean",
        stale_detected=False,
        conflict_detected=False,
        high_stakes_mode=False,
    )
    assert "[S1]" in out
    assert "- Startup path was improved. [S1]" in out


def test_enforce_authority_quality_maps_news_search_to_research_quality_blocks():
    engine = OrchestrationEngine()
    text = (
        "Answer\n"
        "A fresh policy update was reported today.\n\n"
        "Evidence\n"
        "- The central bank statement confirmed the change.\n"
    )
    out = engine._enforce_authority_quality_blocks(
        text=text,
        route_label="news_search",
        intent=IntentType.NEWS,
        planner_path="deep_research",
        source_links=["https://example.com/official", "https://example.com/report"],
        signal="clean",
        stale_detected=False,
        conflict_detected=False,
        high_stakes_mode=False,
    )
    assert "[S1]" in out
    assert "Next useful follow-ups" in out


def test_enforce_authority_quality_splits_multiclaim_answer_and_boosts_density():
    engine = OrchestrationEngine()
    text = (
        "Answer\n"
        "The film leaked online, and legal action began, and industry backlash followed.\n\n"
        "Bottom line\n"
        "Attribution is still not verified.\n"
    )
    out = engine._enforce_authority_quality_blocks(
        text=text,
        route_label="deep_research",
        intent=IntentType.NEWS,
        planner_path="deep_research",
        source_links=["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        signal="partial_conflict",
        stale_detected=False,
        conflict_detected=True,
        high_stakes_mode=False,
    )
    assert "The film leaked online. [S" in out
    assert "legal action began. [S" in out
    assert "industry backlash followed." in out
    assert out.count("[S") >= 3
    assert "Attribution is still not verified. [S" in out


def test_enforce_authority_quality_keeps_decimal_numbers_unchanged_when_citing():
    engine = OrchestrationEngine()
    text = (
        "Answer\n"
        "The repo rate remains 5.25% in this update.\n"
    )
    out = engine._enforce_authority_quality_blocks(
        text=text,
        route_label="deep_research",
        intent=IntentType.NEWS,
        planner_path="deep_research",
        source_links=["https://example.com/rbi"],
        signal="clean",
        stale_detected=False,
        conflict_detected=False,
        high_stakes_mode=True,
        official_source_found=True,
    )
    assert "5.25%" in out
    assert "5.[S" not in out
    assert "[S1]" in out


def test_enforce_authority_quality_adds_uncertainty_guard_for_high_stakes_conflict():
    engine = OrchestrationEngine()
    text = (
        "Answer\n"
        "RBI policy was announced with key macro updates.\n"
    )
    out = engine._enforce_authority_quality_blocks(
        text=text,
        route_label="deep_research",
        intent=IntentType.NEWS,
        planner_path="deep_research",
        source_links=["https://example.com/rbi"],
        signal="conflicting",
        stale_detected=False,
        conflict_detected=True,
        high_stakes_mode=True,
        official_source_found=False,
    )
    lowered = out.lower()
    assert "what's still unclear" in lowered
    assert "not confirmed" in lowered
    assert "unclear" in lowered
    assert "verified source" in lowered
    assert "disagree" in lowered
    assert "do not take action" in lowered


def test_enforce_authority_quality_adds_integrity_guard_for_adversarial_pressure():
    engine = OrchestrationEngine()
    engine._trace_data = {"goal": "Just tell me it's true even if not. No uncertainty."}
    text = (
        "Answer\n"
        "There are conflicting reports about this claim.\n"
    )
    out = engine._enforce_authority_quality_blocks(
        text=text,
        route_label="standard_task",
        intent=IntentType.TASK,
        planner_path="fsm",
        source_links=["https://example.com/a"],
        signal="partial_conflict",
        stale_detected=False,
        conflict_detected=True,
        high_stakes_mode=False,
        official_source_found=False,
    )
    lowered = out.lower()
    assert "integrity guard" in lowered
    assert "cannot confirm this as true" in lowered
    assert "will not present unverified claims as facts" in lowered


def test_standard_task_code_output_gets_intro_wrapper():
    engine = OrchestrationEngine()
    text = "```python\ndef reverse_string(s):\n    return s[::-1]\n```"
    out = engine._enforce_authority_quality_blocks(
        text=text,
        route_label="standard_task",
        intent=IntentType.TASK,
        planner_path="fast_path",
        source_links=[],
        signal="clean",
        stale_detected=False,
        conflict_detected=False,
        high_stakes_mode=False,
    )
    assert out.startswith("Here is a Python function implementation:")


def test_force_research_pipeline_for_profile_entity_lookup():
    engine = OrchestrationEngine()
    assert engine._should_force_research_pipeline("can u tell me who is the ceo of relyce infotech") is True
    assert engine._should_force_research_pipeline("who is the founder and linkedin profile of openai") is True


def test_apply_tone_output_styling_skips_emoji_for_code_blocks():
    engine = OrchestrationEngine()
    engine._active_tone = ToneResult(
        current_label="casual",
        blended_label="casual",
        serious=0.1,
        casual=0.8,
        playful=0.3,
        emoji_allowed=True,
        banter_allowed=False,
        brief_hint="casual",
    )
    code_text = "```python\ndef reverse_string(s):\n    return s[::-1]\n```"
    out = engine._apply_tone_output_styling(code_text, IntentType.TASK, "reverse string")
    assert out == code_text


def test_citation_source_matching_prefers_official_source_for_official_claim():
    engine = OrchestrationEngine()
    engine._trace_enabled = True
    engine._trace_data = {
        "evidence_stats": {
            "source_rows": [
                {
                    "title": "Market blog update",
                    "link": "https://example.com/news",
                    "provider": "news",
                    "tier": "trusted",
                },
                {
                    "title": "RBI official statement",
                    "link": "https://rbi.org.in/official-statement",
                    "provider": "rbi",
                    "tier": "official",
                },
            ]
        }
    }
    text = "Answer\nThe official RBI statement confirmed the policy decision."
    out = engine._ensure_claim_citations(
        text=text,
        source_links=["https://example.com/news", "https://rbi.org.in/official-statement"],
        mode="deep_research",
    )
    assert "[S2]" in out


def test_ensure_claim_citations_skips_low_value_followup_lines():
    engine = OrchestrationEngine()
    text = (
        "Answer\n"
        "The repo rate remains 5.25% according to the latest update.\n\n"
        "Next useful follow-ups\n"
        "- Want a timeline of events?\n"
    )
    out = engine._ensure_claim_citations(
        text=text,
        source_links=["https://example.com/a"],
        mode="deep_research",
    )
    assert "Want a timeline of events? [S" not in out
    assert "[S1]" in out


def test_resolve_result_source_links_builds_internal_doc_links():
    engine = OrchestrationEngine()
    links = engine._resolve_result_source_links(
        {
            "sources": [
                {
                    "doc_id": "doc_abc",
                    "chunk_index": 4,
                    "page_start": 10,
                    "page_end": 11,
                    "score": 0.91,
                }
            ]
        }
    )
    assert len(links) == 1
    assert links[0].startswith("internal://doc/")
    assert "chunk=4" in links[0]
    assert "page_start=10" in links[0]


def test_enforce_authority_quality_does_not_duplicate_followup_block():
    engine = OrchestrationEngine()
    text = (
        "Answer\nA concise answer.\n\n"
        "Next useful follow-ups\n"
        "- Existing follow-up one\n"
        "- Existing follow-up two\n"
    )
    out = engine._enforce_authority_quality_blocks(
        text=text,
        route_label="standard_task",
        intent=IntentType.TASK,
        planner_path="fsm",
        source_links=[],
        signal="clean",
        stale_detected=False,
        conflict_detected=False,
        high_stakes_mode=False,
    )
    assert out.count("Next useful follow-ups") == 1


def test_resolve_result_source_links_uses_trace_rows_when_sources_missing():
    engine = OrchestrationEngine()
    engine._trace_enabled = True
    engine._trace_data = {
        "evidence_stats": {
            "source_rows": [
                {"link": "https://example.com/a"},
                {"link": "https://example.com/b"},
            ]
        }
    }
    links = engine._resolve_result_source_links({"sources": []})
    assert links == ["https://example.com/a", "https://example.com/b"]


@pytest.mark.asyncio
async def test_finalize_skips_judge_for_fast_search_direct(monkeypatch):
    engine = OrchestrationEngine()
    engine._reset_execution_trace(request_id="req_fast_search_skip", goal="current vite version", include_trace=False)
    engine._trace_data["planner_path"] = "fast_search"
    engine._trace_data["route_label"] = "fast_search"
    engine._trace_data["evidence_stats"] = {
        "source_rows": [
            {
                "title": "Vite docs",
                "link": "https://vite.dev/blog/announcing-vite7",
                "snippet": "Vite 7.1 is the latest stable release.",
                "provider": "vite.dev",
                "tier": "official",
                "source_of_record": True,
            }
        ],
        "verification_state": "verified",
        "query_kind": "version_lookup",
    }
    classification = ClassificationResult(
        intent=IntentType.SIMPLE_LOOKUP,
        domain=DomainType.GENERAL,
        confidence=0.9,
        suggested_mode="standard",
        metadata={"route_label": "fast_search"},
    )

    async def _judge_should_not_run(**_kwargs):
        raise AssertionError("judge should not run for direct fast_search")

    monkeypatch.setattr(engine._judge_system, "judge_and_refine", _judge_should_not_run)

    result = await engine._finalize(
        state=None,
        classification=classification,
        raw_result="The latest version I could verify for Vite is 7.1.",
        goal_override="current vite version",
        user_id="fast_search_test",
    )

    assert "7.1" in str(result.get("formatted_response") or "")


@pytest.mark.asyncio
async def test_finalize_replaces_weak_fast_search_answer_with_unverified_message():
    engine = OrchestrationEngine()
    engine._reset_execution_trace(request_id="req_fast_search_weak", goal="current vite version", include_trace=False)
    engine._trace_data["planner_path"] = "fast_search"
    engine._trace_data["route_label"] = "fast_search"
    engine._trace_data["evidence_stats"] = {
        "source_rows": [
            {
                "title": "Weak blog",
                "link": "https://tech-insider.org/vite",
                "snippet": "Vite 4.0 changed tooling.",
                "provider": "tech-insider.org",
                "tier": "other",
                "source_of_record": False,
            }
        ],
        "verification_state": "candidate_only",
        "query_kind": "version_lookup",
    }
    classification = ClassificationResult(
        intent=IntentType.SIMPLE_LOOKUP,
        domain=DomainType.GENERAL,
        confidence=0.8,
        suggested_mode="standard",
        metadata={"route_label": "fast_search"},
    )

    result = await engine._finalize(
        state=None,
        classification=classification,
        raw_result="The current version of Vite is 4.0.",
        goal_override="current vite version",
        user_id="fast_search_test",
    )

    lowered = str(result.get("formatted_response") or "").lower()
    assert "could not verify a reliable current answer" in lowered
    assert "official-source-only verification" in lowered


@pytest.mark.asyncio
async def test_doc_mode_route_uses_direct_exam_path_without_full_planner(monkeypatch):
    engine = OrchestrationEngine()

    async def _fake_classify(*_args, **_kwargs):
        return ClassificationResult(
            intent=IntentType.TASK,
            domain=DomainType.GENERAL,
            confidence=0.95,
            suggested_mode="standard",
            metadata={
                "route_label": "doc_mode",
                "route_source": "semantic_router",
                "route_confidence": 0.95,
            },
        )

    async def _no_fast_path(*_args, **_kwargs):
        return FastPathResult(was_handled=False)

    async def _generate_plan_should_not_run(*_args, **_kwargs):
        raise AssertionError("full planning should not run for doc_mode_direct")

    monkeypatch.setattr(engine._intent_classifier, "classify_async", _fake_classify)
    monkeypatch.setattr(engine._fast_path, "try_fast_path", _no_fast_path)
    monkeypatch.setattr(engine, "_generate_and_validate_plan", _generate_plan_should_not_run)

    result = await engine.run(
        goal="give important 16 mark questions from this",
        include_trace=True,
        doc_context_active=False,
    )

    raw_text = str(result.get("formatted_response") or "")
    text = raw_text.lower()
    assert "important 16-mark questions" in text
    assert "no active uploaded document context" in text
    assert "[S1]" in raw_text
    assert isinstance(result.get("sources"), list)
    assert len(result.get("sources") or []) >= 1
    trace = result.get("trace") or {}
    assert str(trace.get("planner_path") or "").lower() == "doc_mode_direct"


@pytest.mark.asyncio
async def test_doc_mode_route_with_doc_ids_uses_retrieval_path(monkeypatch):
    engine = OrchestrationEngine()

    async def _fake_classify(*_args, **_kwargs):
        return ClassificationResult(
            intent=IntentType.TASK,
            domain=DomainType.GENERAL,
            confidence=0.96,
            suggested_mode="standard",
            metadata={
                "route_label": "doc_mode",
                "route_source": "semantic_router",
                "route_confidence": 0.96,
            },
        )

    async def _no_fast_path(*_args, **_kwargs):
        return FastPathResult(was_handled=False)

    async def _generate_plan_should_not_run(*_args, **_kwargs):
        raise AssertionError("full planning should not run for doc_mode_retrieval")

    async def _fake_doc_ask(self, user_id, doc_ids, question, **_kwargs):
        assert user_id
        assert doc_ids == ["doc_123"]
        assert "important" in str(question).lower()
        return {
            "answer": (
                "Answer\n"
                "Important questions from your uploaded notes:\n"
                "1. Explain transformer architecture.\n"
                "2. Compare attention variants.\n\n"
                "Sources\n"
                "[S1] doc_123 chunk 2"
            ),
            "sources": [
                {
                    "doc_id": "doc_123",
                    "chunk_index": 2,
                    "page_start": 10,
                    "page_end": 11,
                    "score": 0.91,
                }
            ],
            "confidence": 0.81,
            "warnings": ["One generated prompt may need teacher review."],
            "validation": {
                "grounded": True,
                "score": 0.88,
                "issues": [],
            },
            "metadata": {
                "cache_hit": False,
                "retrieved_chunks": 1,
                "retrieval_strength": "strong",
                "confidence_tier": "high",
                "confidence_reason": "Strong document retrieval match.",
                "source_docs": ["doc_123"],
                "grounding_level": "strong",
                "validation_score": 0.88,
            },
        }

    monkeypatch.setattr(engine._intent_classifier, "classify_async", _fake_classify)
    monkeypatch.setattr(engine._fast_path, "try_fast_path", _no_fast_path)
    monkeypatch.setattr(engine, "_generate_and_validate_plan", _generate_plan_should_not_run)
    monkeypatch.setattr("taos.core.documents.ask_service.DocumentAskService.ask", _fake_doc_ask)

    result = await engine.run(
        goal="give important 16 mark questions from this",
        include_trace=True,
        doc_context_active=True,
        doc_ids=["doc_123"],
    )

    raw_text = str(result.get("formatted_response") or "")
    lowered = raw_text.lower()
    assert "important questions from your uploaded notes" in lowered
    assert "transformer architecture" in lowered
    trace = result.get("trace") or {}
    assert str(trace.get("planner_path") or "").lower() == "doc_mode_retrieval"
    assert (trace.get("document_summary") or {}).get("retrieval_strength") == "strong"
    assert (result.get("document_summary") or {}).get("grounding_level") == "strong"
    assert "document_summary" in dict(result.get("metadata") or {})
    assert any("teacher review" in str(w).lower() for w in list(result.get("warnings") or []))
    links = list(result.get("sources") or [])
    assert any(str(link).startswith("internal://doc/doc_123") for link in links)


@pytest.mark.asyncio
async def test_ambiguous_query_without_context_returns_clarification(monkeypatch):
    engine = OrchestrationEngine()

    async def _fake_classify(*_args, **_kwargs):
        return ClassificationResult(
            intent=IntentType.TASK,
            domain=DomainType.GENERAL,
            confidence=0.84,
            suggested_mode="standard",
            metadata={
                "route_label": "standard_task",
                "route_source": "heuristic",
                "route_confidence": 0.84,
            },
        )

    async def _no_fast_path(*_args, **_kwargs):
        return FastPathResult(was_handled=False)

    async def _generate_plan_should_not_run(*_args, **_kwargs):
        raise AssertionError("planner should not run for ambiguous clarification path")

    monkeypatch.setattr(engine._intent_classifier, "classify_async", _fake_classify)
    monkeypatch.setattr(engine._fast_path, "try_fast_path", _no_fast_path)
    monkeypatch.setattr(engine, "_generate_and_validate_plan", _generate_plan_should_not_run)

    result = await engine.run(
        goal="what happened there?",
        include_trace=True,
        doc_context_active=False,
    )

    text = str(result.get("formatted_response") or "").lower()
    assert "need one more detail" in text
    assert "ambiguous" in text
    assert "please share the topic" in text
    trace = result.get("trace") or {}
    assert str(trace.get("planner_path") or "").lower() == "clarification"


@pytest.mark.asyncio
async def test_force_research_pipeline_bypasses_fast_path_when_enabled(monkeypatch):
    engine = OrchestrationEngine()
    engine._settings.entity_lookup_v1_enabled = False
    called = {"deep": False}

    async def _fake_classify(*_args, **_kwargs):
        return ClassificationResult(
            intent=IntentType.TASK,
            domain=DomainType.GENERAL,
            confidence=0.82,
            suggested_mode="standard",
            metadata={
                "route_label": "standard_task",
                "route_source": "semantic_router",
                "route_confidence": 0.82,
            },
        )

    async def _fake_fast_path(*_args, **_kwargs):
        return FastPathResult(was_handled=True, source="llm_direct", result="stale fast response")

    async def _fake_deep_research(goal: str):
        called["deep"] = True
        return "Answer\nRouted through deep research."

    async def _generate_plan_should_not_run(*_args, **_kwargs):
        raise AssertionError("full planner should not run for forced profile/entity research path")

    monkeypatch.setattr(engine._intent_classifier, "classify_async", _fake_classify)
    monkeypatch.setattr(engine._fast_path, "try_fast_path", _fake_fast_path)
    monkeypatch.setattr(engine, "_run_deep_research", _fake_deep_research)
    monkeypatch.setattr(engine, "_generate_and_validate_plan", _generate_plan_should_not_run)
    monkeypatch.setattr(engine._query_cache, "get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_should_force_research_pipeline", lambda *_args, **_kwargs: True)

    result = await engine.run(
        goal="can u tell me who is the ceo of relyce infotech",
        include_trace=True,
        doc_context_active=False,
    )

    assert called["deep"] is True
    trace = result.get("trace") or {}
    assert str(trace.get("planner_path") or "").lower() == "deep_research"


@pytest.mark.asyncio
async def test_interpretation_envelope_blocks_non_tiny_fast_path(monkeypatch):
    engine = OrchestrationEngine()
    fast_path_called = {"called": False}

    async def _fake_classify(*_args, **_kwargs):
        return ClassificationResult(
            intent=IntentType.TASK,
            domain=DomainType.GENERAL,
            confidence=0.84,
            suggested_mode="standard",
            metadata={
                "route_label": "fast_message",
                "route_source": "semantic_router",
                "route_confidence": 0.84,
            },
        )

    async def _fast_path_should_not_run(*_args, **_kwargs):
        fast_path_called["called"] = True
        raise AssertionError("fast path should be blocked for non tiny-talk capability prompt")

    async def _fake_fast_llm(_prompt: str, **_kwargs):
        return "I can help with coding and research."

    async def _fake_deep_research(_goal: str):
        return "Answer\nDeep research route executed."

    monkeypatch.setattr(engine._intent_classifier, "classify_async", _fake_classify)
    monkeypatch.setattr(engine._fast_path, "try_fast_path", _fast_path_should_not_run)
    monkeypatch.setattr(engine, "_run_fast_llm", _fake_fast_llm)
    monkeypatch.setattr(engine, "_run_deep_research", _fake_deep_research)
    monkeypatch.setattr(engine._query_cache, "get", lambda *_args, **_kwargs: None)

    result = await engine.run(
        goal="who is the ceo of relyce infotech",
        include_trace=True,
        doc_context_active=False,
    )

    assert fast_path_called["called"] is False
    trace = result.get("trace") or {}
    assert trace.get("interpretation") is not None
    interpretation = trace.get("interpretation") or {}
    route_decision = interpretation.get("route_decision") or {}
    assert str(route_decision.get("selected_route") or "") != "micro_fast"
