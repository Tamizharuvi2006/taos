"""
TAOS Tests — Semantic, Fast Path, Output, Evaluation, Source Ranking.

Tests all PRD NEW features (Phase 12).
"""

from __future__ import annotations

import pytest

from taos.core.semantic.intent_classifier import (
    IntentClassifier,
    IntentType,
    DomainType,
    ClassificationResult,
)
from taos.core.semantic.query_rewriter import QueryRewriter
from taos.core.fast_path.fast_path import FastPathEngine, FastPathCache
from taos.core.output.response_formatter import ResponseFormatter
from taos.core.evaluation.self_evaluator import SelfEvaluator
from taos.core.tools.source_ranker import SourceRanker, RankedSource


# ═══════════════════════════════════════════════════════════
# INTENT CLASSIFIER TESTS
# ═══════════════════════════════════════════════════════════

class TestIntentClassifier:
    @pytest.fixture
    def classifier(self):
        return IntentClassifier()

    # --- Deterministic overrides ---
    def test_news_intent(self, classifier):
        result = classifier.classify("latest AI model releases")
        assert result.intent == IntentType.NEWS
        assert result.domain == DomainType.AI
        assert result.is_deterministic is True

    def test_definition_intent(self, classifier):
        result = classifier.classify("what is a transformer model")
        assert result.intent == IntentType.DEFINITION

    def test_comparison_intent(self, classifier):
        result = classifier.classify("React vs Vue performance")
        assert result.intent == IntentType.COMPARISON
        assert result.domain == DomainType.PROGRAMMING

    def test_transform_intent(self, classifier):
        result = classifier.classify("summarize this")
        assert result.intent == IntentType.TRANSFORM

    def test_simple_lookup_intent(self, classifier):
        result = classifier.classify("current version of vite")
        assert result.intent == IntentType.SIMPLE_LOOKUP

    def test_small_talk_intent_fast_path(self, classifier):
        result = classifier.classify("hey macha")
        assert result.intent == IntentType.SIMPLE_LOOKUP
        assert result.suggested_mode == "fast"
        assert result.is_deterministic is True
        assert result.metadata.get("route_label") == "fast_message"
        assert result.metadata.get("route_source") == "heuristic"
        assert result.metadata.get("route_confidence") == 1.0

    def test_tamil_translit_how_are_you_is_small_talk(self, classifier):
        result = classifier.classify("epadi irruka")
        assert result.intent == IntentType.SIMPLE_LOOKUP
        assert result.suggested_mode == "fast"
        assert result.is_deterministic is True

    def test_buddy_prefixed_tamil_small_talk_routes_fast_heuristic(self, classifier):
        result = classifier.classify("macha epadi irruka")
        assert result.intent == IntentType.SIMPLE_LOOKUP
        assert result.suggested_mode == "fast"
        assert result.metadata.get("route_label") == "fast_message"
        assert result.metadata.get("route_source") == "heuristic"

    def test_hyyy_routes_as_fast_message(self, classifier):
        result = classifier.classify("hyyy")
        assert result.intent == IntentType.SIMPLE_LOOKUP
        assert result.suggested_mode == "fast"
        assert result.metadata.get("route_label") == "fast_message"

    def test_short_multilingual_greeting_routes_fast_message(self, classifier):
        result = classifier.classify("hola")
        assert result.intent == IntentType.SIMPLE_LOOKUP
        assert result.suggested_mode == "fast"
        assert result.metadata.get("route_label") == "fast_message"
        assert result.metadata.get("route_source") == "heuristic"

    def test_user_status_routes_fast_message(self, classifier):
        result = classifier.classify("i am good")
        assert result.intent == IntentType.SIMPLE_LOOKUP
        assert result.suggested_mode == "fast"
        assert result.metadata.get("route_label") == "fast_message"

    def test_greeting_plus_real_question_not_forced_small_talk(self, classifier):
        result = classifier.classify("hey what is python")
        assert not (result.is_deterministic and result.intent == IntentType.SIMPLE_LOOKUP)

    def test_lifestyle_advice_not_routed_to_task(self, classifier):
        result = classifier.classify("i need to drink coffee what coffee should i drink at night?")
        assert result.intent == IntentType.SIMPLE_LOOKUP
        assert result.suggested_mode == "fast"

    def test_lifestyle_advice_with_typos_still_routes_fast(self, classifier):
        result = classifier.classify("i need to drink an coffe what coffew i need to drink at night ?")
        assert result.intent == IntentType.SIMPLE_LOOKUP
        assert result.suggested_mode == "fast"

    @pytest.mark.parametrize(
        "query",
        ["epdi iruka", "epadi iruka", "epdi irruka", "epadi irruka da", "saptiya", "saptaya", "enna panra", "seri da", "polaama"],
    )
    def test_tamil_translit_small_talk_variants_route_fast_message(self, classifier, query):
        result = classifier.classify(query)
        assert result.intent == IntentType.SIMPLE_LOOKUP
        assert result.suggested_mode == "fast"
        assert result.metadata.get("route_label") == "fast_message"

    def test_buddy_prefixed_saptaya_routes_fast_message(self, classifier):
        result = classifier.classify("macha saptaya")
        assert result.intent == IntentType.SIMPLE_LOOKUP
        assert result.suggested_mode == "fast"
        assert result.metadata.get("route_label") == "fast_message"

    def test_serious_short_query_not_routed_as_fast_message(self, classifier):
        result = classifier.classify("official statement?")
        assert result.metadata.get("route_label") != "fast_message"
        assert result.suggested_mode != "fast"

    def test_multilingual_unknown_phrase_can_route_via_llm_router(self, classifier, monkeypatch):
        async def fake_llm_route_label(_query):
            return "fast_message", 0.91

        monkeypatch.setattr(classifier, "_llm_route_label", fake_llm_route_label)

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(classifier.classify_async("comment ca va"))
        assert result.intent == IntentType.SIMPLE_LOOKUP
        assert result.metadata.get("route_label") == "fast_message"
        assert result.metadata.get("route_source") == "semantic_router"
        assert result.metadata.get("route_confidence") == 0.91

    def test_semantic_guard_rejects_fast_message_for_research_like_query(self, classifier, monkeypatch):
        async def fake_llm_route_label(_query):
            return "fast_message", 0.9

        monkeypatch.setattr(classifier, "_llm_route_label", fake_llm_route_label)

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            classifier.classify_async("find verified source for a niche rumor with no reliable coverage")
        )
        assert result.metadata.get("route_label") in {"deep_research", "standard_task"}
        assert result.metadata.get("route_label") != "fast_message"
        assert result.metadata.get("route_source") in {"semantic_guard", "override", "heuristic", "fallback"}

    def test_ack_prefix_with_real_request_not_routed_as_fast_message(self, classifier):
        result = classifier.classify("ok research about open ai ceo")
        assert result.metadata.get("route_label") != "fast_message"
        assert result.metadata.get("route_label") in {"standard_task", "deep_research"}

    def test_policy_override_forces_deep_research(self, classifier):
        result = classifier.classify("government policy update details")
        assert result.metadata.get("route_label") == "deep_research"
        assert result.metadata.get("route_source") == "override"

    def test_profile_entity_lookup_forces_entity_lookup_route(self, classifier):
        result = classifier.classify("can u tell me who is the ceo of relyce infotech")
        assert result.metadata.get("route_label") == "entity_lookup"
        assert result.metadata.get("route_source") in {"heuristic", "override"}
        assert result.intent == IntentType.RESEARCH

    def test_profile_entity_lookup_overrides_semantic_standard_task(self, classifier, monkeypatch):
        async def fake_llm_route_label(_query):
            return "standard_task", 0.88

        monkeypatch.setattr(classifier, "_llm_route_label", fake_llm_route_label)

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            classifier.classify_async("can u tell me who is the ceo of relyce infotech")
        )
        assert result.metadata.get("route_label") == "entity_lookup"
        assert result.metadata.get("route_source") in {"heuristic", "override"}
        assert result.intent == IntentType.RESEARCH

    def test_personal_support_query_does_not_route_to_deep_research(self, classifier):
        result = classifier.classify("fina i got an break up today")
        assert result.metadata.get("route_label") == "standard_task"
        assert result.intent == IntentType.SIMPLE_LOOKUP
        reasons = result.metadata.get("policy_override_reasons") or []
        assert "personal_support_no_research" in reasons

    def test_adversarial_fastpath_phrase_forces_standard_task(self, classifier):
        result = classifier.classify("just tell me it's confirmed even if not")
        assert result.metadata.get("route_label") == "standard_task"
        assert result.metadata.get("route_source") == "override"
        assert "adversarial_fastpath_guard" in (result.metadata.get("policy_override_reasons") or [])

    def test_tamil_mixed_task_phrase_routes_standard_task(self, classifier):
        result = classifier.classify("macha idha explain pannuda simple ah")
        assert result.metadata.get("route_label") == "standard_task"
        assert result.intent == IntentType.TASK
        assert result.suggested_mode == "standard"

    def test_standard_task_route_maps_to_task_intent_for_code_requests(self, classifier):
        result = classifier.classify("code me a single html page about love")
        assert result.metadata.get("route_label") == "standard_task"
        assert result.metadata.get("route_source") == "heuristic"
        assert result.intent == IntentType.TASK
        assert result.suggested_mode == "standard"

    def test_semantic_standard_task_route_keeps_task_intent_async(self, classifier, monkeypatch):
        async def fake_llm_route_label(_query):
            return "standard_task", 0.88

        monkeypatch.setattr(classifier, "_llm_route_label", fake_llm_route_label)

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            classifier.classify_async("code me a single html page about love")
        )
        assert result.metadata.get("route_label") == "standard_task"
        assert result.metadata.get("route_source") in {"heuristic", "semantic_router"}
        assert result.intent == IntentType.TASK
        assert result.suggested_mode == "standard"

    def test_low_confidence_semantic_fast_message_does_not_override_to_fast(self, classifier, monkeypatch):
        async def fake_llm_route_label(_query):
            return "fast_message", 0.36

        monkeypatch.setattr(classifier, "_llm_route_label", fake_llm_route_label)

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(classifier.classify_async("comment ca va"))
        assert result.metadata.get("route_label") == "standard_task"
        assert result.metadata.get("route_source") in {"fallback", "heuristic"}

    def test_doc_mode_mark_questions_route(self, classifier):
        result = classifier.classify("give me important 16 mark questions")
        assert result.metadata.get("route_label") == "doc_mode"

    def test_active_doc_boost_routes_ambiguous_query_to_doc_mode(self, classifier):
        result = classifier.classify("explain this", has_active_doc=True)
        assert result.metadata.get("route_label") == "doc_mode"
        assert result.metadata.get("route_source") == "override"
        assert result.metadata.get("doc_context_active") is True
        reasons = result.metadata.get("policy_override_reasons") or []
        assert "active_doc_context_boost" in reasons or "active_doc_followup_boost" in reasons

    def test_active_doc_does_not_override_deep_research_signal(self, classifier):
        result = classifier.classify("latest OpenAI news", has_active_doc=True)
        assert result.metadata.get("route_label") == "deep_research"
        assert result.metadata.get("doc_context_active") is True

    def test_active_doc_does_not_override_fast_message_signal(self, classifier):
        result = classifier.classify("hey", has_active_doc=True)
        assert result.metadata.get("route_label") == "fast_message"
        assert result.metadata.get("doc_context_active") is True

    def test_active_doc_followup_boost_routes_next_to_doc_mode(self, classifier):
        result = classifier.classify("next", has_active_doc=True)
        assert result.metadata.get("route_label") == "doc_mode"
        assert result.metadata.get("route_source") == "override"
        assert "active_doc_followup_boost" in (result.metadata.get("policy_override_reasons") or [])

    # --- Domain detection ---
    def test_ai_domain(self, classifier):
        result = classifier.classify("explain how GPT-4 works")
        assert result.domain == DomainType.AI

    def test_programming_domain(self, classifier):
        result = classifier.classify("create a python script")
        assert result.domain == DomainType.PROGRAMMING

    def test_startup_domain(self, classifier):
        result = classifier.classify("how to get VC funding for startup")
        assert result.domain == DomainType.STARTUP

    # --- Mode selection ---
    def test_fast_mode_for_lookup(self, classifier):
        result = classifier.classify("what version of python")
        assert result.suggested_mode == "fast"

    def test_standard_mode_for_task(self, classifier):
        result = classifier.classify("build a REST API with FastAPI and deploy it")
        assert result.suggested_mode == "standard"

    def test_deep_mode_for_research(self, classifier):
        result = classifier.classify("research the latest advances in transformer architectures")
        assert result.suggested_mode == "deep"
        assert result.metadata.get("route_label") == "deep_research"

    def test_typo_research_token_routes_as_research_mode(self, classifier):
        result = classifier.classify("vresearch about open ai ce")
        assert result.metadata.get("route_label") == "deep_research"
        assert result.intent in {IntentType.RESEARCH, IntentType.NEWS}
        # With typo-aware research markers, deep-research route should map to research intent.
        assert result.intent == IntentType.RESEARCH
        assert result.suggested_mode == "deep"

    def test_doc_mode_label_for_uploaded_doc_queries(self, classifier):
        result = classifier.classify("from this PDF give me important questions")
        assert result.metadata.get("route_label") == "doc_mode"

    # --- Follow-up detection ---
    def test_followup_detected(self, classifier):
        result = classifier.classify("also show me the pricing", has_context=True)
        assert result.is_followup is True

    def test_no_followup_without_context(self, classifier):
        result = classifier.classify("also show me the pricing", has_context=False)
        assert result.is_followup is False

    # --- Confidence ---
    def test_deterministic_full_confidence(self, classifier):
        result = classifier.classify("what is a REST API")
        assert result.confidence == 1.0
        assert result.is_deterministic is True

    def test_heuristic_lower_confidence(self, classifier):
        result = classifier.classify("some very ambiguous query here about things")
        assert result.confidence < 1.0


# ═══════════════════════════════════════════════════════════
# QUERY REWRITER TESTS
# ═══════════════════════════════════════════════════════════

class TestQueryRewriter:
    @pytest.fixture
    def rewriter(self):
        return QueryRewriter()

    def test_no_rewrite_needed(self, rewriter):
        result = rewriter.rewrite("build a REST API", IntentType.TASK, DomainType.PROGRAMMING)
        assert result.rewritten == "build a REST API"
        assert result.was_modified is False

    def test_news_recency_qualifier(self, rewriter):
        result = rewriter.rewrite("AI model releases", IntentType.NEWS, DomainType.AI)
        assert "2026" in result.rewritten or "latest" in result.rewritten
        assert result.was_modified is True

    def test_news_recency_not_added_for_profile_entity_lookup(self, rewriter):
        result = rewriter.rewrite(
            "can u tell me who is the ceo of relyce infotech",
            IntentType.NEWS,
            DomainType.GENERAL,
        )
        assert "(latest 2026)" not in result.rewritten.lower()

    def test_research_depth_not_added_for_profile_entity_lookup(self, rewriter):
        result = rewriter.rewrite(
            "can u tell me who is the ceo of relyce infotech",
            IntentType.RESEARCH,
            DomainType.GENERAL,
        )
        assert not result.rewritten.lower().startswith("comprehensive ")

    def test_comparison_normalization(self, rewriter):
        result = rewriter.rewrite("React vs Vue", IntentType.COMPARISON, DomainType.PROGRAMMING)
        assert "comparison" in result.rewritten.lower()
        assert result.was_modified is True

    def test_research_depth_qualifier(self, rewriter):
        result = rewriter.rewrite("transformer architectures", IntentType.RESEARCH, DomainType.AI)
        assert "comprehensive" in result.rewritten.lower()
        assert result.was_modified is True

    def test_followup_expansion(self, rewriter):
        result = rewriter.rewrite(
            "summarize this",
            IntentType.TRANSFORM,
            DomainType.GENERAL,
            previous_context="Python 3.12 has many new features including...",
            is_followup=True,
        )
        assert result.was_modified is True
        assert result.context_injected is True

    def test_fragment_canonicalization_for_older_one(self, rewriter):
        result = rewriter.rewrite(
            "older one what done",
            IntentType.TASK,
            DomainType.GENERAL,
            previous_context="Phase 93.2 implemented reliability and routing hardening.",
            is_followup=True,
        )
        assert result.was_modified is True
        assert "previously discussed" in result.rewritten.lower() or "previously discussed item" in result.rewritten.lower()

    def test_doc_fragment_canonicalization(self, rewriter):
        result = rewriter.rewrite(
            "from this pdf important questions",
            IntentType.TASK,
            DomainType.GENERAL,
        )
        assert result.was_modified is True
        assert "uploaded document context" in result.rewritten.lower()

    def test_research_fragment_canonicalization(self, rewriter):
        result = rewriter.rewrite(
            "openai leadership latest official status",
            IntentType.TASK,
            DomainType.GENERAL,
        )
        assert result.was_modified is True
        assert "research and verify" in result.rewritten.lower()


# ═══════════════════════════════════════════════════════════
# FAST PATH TESTS
# ═══════════════════════════════════════════════════════════

class TestFastPathCache:
    def test_put_and_get(self):
        cache = FastPathCache(capacity=10)
        cache.put("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_miss(self):
        cache = FastPathCache()
        assert cache.get("nonexistent") is None

    def test_ttl_expiry(self):
        import time

        cache = FastPathCache(ttl_seconds=0)  # Instant expiry
        cache.put("key1", "value1")
        time.sleep(0.01)
        assert cache.get("key1") is None

    def test_capacity_eviction(self):
        cache = FastPathCache(capacity=2)
        cache.put("k1", "v1")
        cache.put("k2", "v2")
        cache.put("k3", "v3")
        assert cache.stats["size"] <= 2

    def test_stats(self):
        cache = FastPathCache()
        cache.put("k1", "v1")
        cache.get("k1")
        cache.get("miss")
        assert cache.stats["hits"] == 1
        assert cache.stats["misses"] == 1


class TestFastPathEngine:
    @pytest.fixture
    def engine(self):
        return FastPathEngine()

    def test_should_fast_path_simple_lookup(self, engine):
        cls = ClassificationResult(intent=IntentType.SIMPLE_LOOKUP, domain=DomainType.GENERAL, confidence=1.0)
        assert engine.should_fast_path(cls) is True

    def test_should_fast_path_definition(self, engine):
        cls = ClassificationResult(intent=IntentType.DEFINITION, domain=DomainType.GENERAL, confidence=1.0)
        assert engine.should_fast_path(cls) is True

    def test_should_not_fast_path_task(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=1.0)
        assert engine.should_fast_path(cls) is False

    def test_direct_generation_task_can_fast_path(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.PROGRAMMING, confidence=0.9)
        assert engine.should_fast_path(cls, query="code me a single html page about love") is True

    def test_should_not_fast_path_low_confidence(self, engine):
        cls = ClassificationResult(intent=IntentType.SIMPLE_LOOKUP, domain=DomainType.GENERAL, confidence=0.5)
        assert engine.should_fast_path(cls) is False

    def test_transform_needs_followup(self, engine):
        cls = ClassificationResult(intent=IntentType.TRANSFORM, domain=DomainType.GENERAL, confidence=1.0, is_followup=False)
        assert engine.should_fast_path(cls) is False

        cls.is_followup = True
        assert engine.should_fast_path(cls) is True

    def test_cache_hit(self, engine):
        engine.cache_result("test query", "cached answer")
        cls = ClassificationResult(intent=IntentType.SIMPLE_LOOKUP, domain=DomainType.GENERAL, confidence=1.0)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path("test query", cls))
        assert result.was_handled is True
        assert result.result == "cached answer"
        assert result.source == "cache"

    def test_small_talk_rule_bypasses_llm(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path("hey therre", cls))
        assert result.was_handled is True
        assert isinstance(result.result, str)
        assert result.result
        assert result.source == "small_talk_rule"

    def test_small_talk_rule_user_status_bypasses_llm(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path("i am good", cls))
        assert result.was_handled is True
        assert isinstance(result.result, str)
        assert result.source == "small_talk_rule"

    def test_small_talk_rule_capability_ask_bypasses_llm(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path("how well u can code?", cls))
        assert result.was_handled is True
        assert isinstance(result.result, str)
        assert "code" in result.result.lower()
        assert result.source == "small_talk_rule"

    def test_small_talk_rule_capability_rating_phrase_detected(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            engine.try_fast_path("how well u can code react on the basis of 1-10", cls)
        )
        assert result.was_handled is True
        assert isinstance(result.result, str)
        assert "/10" in result.result or "10" in result.result
        assert result.source == "small_talk_rule"

    def test_small_talk_rule_research_capability_typo_bypasses_llm(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path("how wek u can reserch ?", cls))
        assert result.was_handled is True
        assert isinstance(result.result, str)
        assert "research" in result.result.lower() or "source" in result.result.lower()
        assert result.source == "small_talk_rule"

    def test_small_talk_rule_does_not_hijack_real_question(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path("hey what is python", cls))
        assert result.was_handled is False

    def test_small_talk_rule_does_not_hijack_ack_prefix_with_research_request(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path("ok research about open ai ceo", cls))
        assert result.was_handled is False

    def test_requires_tools_for_profile_lookup_queries(self, engine):
        assert engine.requires_tools("research about openai ceo linkedin profile") is True
        assert engine.requires_tools("who is the current openai ceo") is True

    def test_small_talk_rule_tamil_translit_bypasses_llm(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path("epadi irruka", cls))
        assert result.was_handled is True
        assert isinstance(result.result, str)
        assert "epadi" in result.result.lower() or "nalla" in result.result.lower()
        assert result.source == "small_talk_rule"

    def test_small_talk_rule_how_are_you_doing_bypasses_llm(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path("hey macha how are you doing", cls))
        assert result.was_handled is True
        assert isinstance(result.result, str)
        assert result.source == "small_talk_rule"

    @pytest.mark.parametrize("query", ["saptiya", "saptaya", "macha saptaya", "enna panra", "seri da", "polaama"])
    def test_small_talk_rule_tamil_short_variants_bypass_llm(self, engine, query):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path(query, cls))
        assert result.was_handled is True
        assert isinstance(result.result, str)
        assert result.source == "small_talk_rule"

    def test_small_talk_rule_does_not_hijack_serious_short_query(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path("official statement?", cls))
        assert result.was_handled is False

    def test_small_talk_rule_short_multilingual_ping_bypasses_llm(self, engine):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path("hola", cls))
        assert result.was_handled is True
        assert result.source == "small_talk_rule"

    @pytest.mark.parametrize(
        "query",
        ["ok bro", "nice da", "cool", "got it", "understood", "sounds good"],
    )
    def test_small_talk_rule_ack_variants_bypass_llm(self, engine, query):
        cls = ClassificationResult(intent=IntentType.TASK, domain=DomainType.GENERAL, confidence=0.2)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.try_fast_path(query, cls))
        assert result.was_handled is True
        assert result.source == "small_talk_rule"

    def test_micro_fast_response_is_strict_tiny_talk_only(self, engine):
        assert engine.micro_fast_response("hey") is not None
        assert engine.micro_fast_response("thanks") is not None
        assert engine.micro_fast_response("how are u macha") is not None
        assert engine.micro_fast_response("how well u can code react?") is None
        assert engine.micro_fast_response("who is openai ceo") is None


# ═══════════════════════════════════════════════════════════
# RESPONSE FORMATTER TESTS
# ═══════════════════════════════════════════════════════════

class TestResponseFormatter:
    @pytest.fixture
    def formatter(self):
        return ResponseFormatter()

    def test_basic_formatting(self, formatter):
        result = formatter.format(
            raw_output="Python 3.12 was released in October 2023.",
            goal="When was Python 3.12 released?",
            confidence=0.9,
        )
        assert result.full_text == "Python 3.12 was released in October 2023."
        assert "High" in result.confidence_display

    def test_adaptive_lookup_formatting(self, formatter):
        result = formatter.format(
            raw_output="Python 3.12 was released in October 2023. It includes many new features. Here is a bullet point: \n- Cool feature",
            goal="When was Python 3.12 released?",
            intent=IntentType.SIMPLE_LOOKUP
        )
        assert "- Cool feature" not in result.full_text
        assert result.full_text.startswith("Python 3.12 was released in October 2023")

    def test_strips_internal_logs(self, formatter):
        raw = "[DEBUG] Processing step 1\nThe answer is 42.\n[TRACE] Completed.\n"
        result = formatter.format(raw_output=raw, goal="What is the answer?")
        assert "[DEBUG]" not in result.full_text
        assert "[TRACE]" not in result.full_text

    def test_preserves_natural_structure_for_comparison(self, formatter):
        raw = "Overview:\n- Point one is important\n- Point two matters\n- Point three also"
        result = formatter.format(raw_output=raw, goal="Overview", intent=IntentType.COMPARISON)
        assert "- Point one is important" in result.full_text

    def test_confidence_display_high(self, formatter):
        result = formatter.format(raw_output="Answer", goal="Q", confidence=0.95)
        assert "High" in result.confidence_display

    def test_confidence_display_low(self, formatter):
        result = formatter.format(raw_output="Answer", goal="Q", confidence=0.3)
        assert "Low" in result.confidence_display

    def test_length_limit_by_intent(self, formatter):
        long_output = "word " * 2000
        result = formatter.format(
            raw_output=long_output,
            goal="Q",
            intent=IntentType.SIMPLE_LOOKUP,
        )
        assert result.word_count <= 50  # MAX_WORDS[SIMPLE_LOOKUP] is 40

    def test_no_forced_period_after_exclamation(self, formatter):
        result = formatter.format(
            raw_output="Hello!",
            goal="hello",
            intent=IntentType.SIMPLE_LOOKUP,
        )
        assert result.full_text == "Hello!"


# ═══════════════════════════════════════════════════════════
# SELF-EVALUATOR TESTS
# ═══════════════════════════════════════════════════════════

class TestSelfEvaluator:
    @pytest.fixture
    def evaluator(self):
        return SelfEvaluator()

    def test_good_output_passes(self, evaluator):
        result = evaluator.evaluate(
            output="Python 3.12 was released in October 2023. Key features include better error messages and performance improvements.",
            goal="When was Python 3.12 released?",
        )
        assert result.passed is True
        assert result.overall_score >= 0.5

    def test_empty_output_low_scores(self, evaluator):
        result = evaluator.evaluate(output="x", goal="Tell me about Python programming language features")
        assert result.completeness_score < 0.8

    def test_contradictions_detected(self, evaluator):
        result = evaluator.evaluate(
            output="The operation succeeded. The operation failed.",
            goal="Did it work?",
        )
        assert result.correctness_score < 1.0

    def test_incomplete_output_detected(self, evaluator):
        result = evaluator.evaluate(
            output="The weather is nice today.",
            goal="Compare Python and Rust performance benchmarks with detailed analysis",
        )
        assert result.completeness_score < 0.8

    def test_good_structure_bonus(self, evaluator):
        structured = "Overview:\n- Point 1 about features\n- Point 2 about performance\n- Point 3 about ecosystem"
        unstructured = "Features are good. Performance is good. Ecosystem is good."
        r1 = evaluator.evaluate(output=structured, goal="Overview of Python")
        r2 = evaluator.evaluate(output=unstructured, goal="Overview of Python")
        assert r1.clarity_score >= r2.clarity_score

    def test_placeholder_penalized(self, evaluator):
        result = evaluator.evaluate(
            output="[TODO] implement this section. [PLACEHOLDER] content here.",
            goal="Explain Python",
        )
        assert result.clarity_score < 0.7


# ═══════════════════════════════════════════════════════════
# SOURCE RANKER TESTS
# ═══════════════════════════════════════════════════════════

class TestSourceRanker:
    @pytest.fixture
    def ranker(self):
        return SourceRanker()

    @pytest.fixture
    def sample_results(self):
        return [
            {"url": "https://docs.python.org/3/whatsnew/3.12.html", "title": "What's New in Python 3.12", "snippet": "Detailed list of new features..."},
            {"url": "https://stackoverflow.com/questions/12345", "title": "Python 3.12 features", "snippet": "Discussion about features..."},
            {"url": "https://randomsite.com/python", "title": "Python stuff", "snippet": "Some info"},
            {"url": "https://github.com/python/cpython", "title": "CPython repo", "snippet": "The reference implementation"},
        ]

    def test_ranks_official_first(self, ranker, sample_results):
        ranked = ranker.rank(sample_results, domain=DomainType.PROGRAMMING)
        assert ranked[0].url.startswith("https://docs.python.org")
        assert ranked[0].tier == "official"

    def test_trusted_ranks_above_other(self, ranker, sample_results):
        ranked = ranker.rank(sample_results, domain=DomainType.PROGRAMMING)
        tiers = [r.tier for r in ranked]
        # Official should come before other
        if "official" in tiers and "other" in tiers:
            assert tiers.index("official") < tiers.index("other")

    def test_blocked_domains_excluded(self, ranker):
        results = [
            {"url": "https://spam-example.com/page", "title": "Spam"},
            {"url": "https://docs.python.org/3/", "title": "Python Docs"},
        ]
        ranked = ranker.rank(results)
        urls = [r.url for r in ranked]
        assert "https://spam-example.com/page" not in urls

    def test_provider_diversity(self, ranker):
        results = [
            {"url": "https://github.com/repo1", "title": "Repo 1"},
            {"url": "https://github.com/repo2", "title": "Repo 2"},
            {"url": "https://github.com/repo3", "title": "Repo 3"},
            {"url": "https://stackoverflow.com/q1", "title": "Question"},
        ]
        ranked = ranker.rank(results)
        github_count = sum(1 for r in ranked if "github.com" in r.url)
        assert github_count <= 2  # Max 2 per provider

    def test_max_results_limit(self, ranker, sample_results):
        ranked = ranker.rank(sample_results, max_results=2)
        assert len(ranked) <= 2

    def test_filter_irrelevant(self, ranker):
        sources = [
            RankedSource(url="https://a.com", rank_score=0.9),
            RankedSource(url="https://b.com", rank_score=0.1),
        ]
        filtered = ranker.filter_irrelevant(sources, min_score=0.5)
        assert len(filtered) == 1

    def test_ai_domain_sources(self, ranker):
        results = [
            {"url": "https://arxiv.org/abs/2301.12345", "title": "GPT-5 Paper"},
            {"url": "https://randomsite.com/ai", "title": "AI stuff"},
        ]
        ranked = ranker.rank(results, domain=DomainType.AI)
        assert ranked[0].tier == "official"

    def test_invalid_calendar_date_in_snippet_does_not_crash(self, ranker):
        results = [
            {
                "url": "https://example.com/post",
                "title": "AI leadership update",
                "snippet": "Reported on April 31, 2026 with interview details.",
            }
        ]
        ranked = ranker.rank(results, domain=DomainType.AI)
        assert len(ranked) == 1
