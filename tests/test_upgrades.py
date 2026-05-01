"""
TAOS Tests — Final 5 Upgrades.

Tests: Template Formatter, Latency Optimizer, Progress Tracker, Source Ranker upgrades.
"""

from __future__ import annotations

import time
import pytest

from taos.core.output.templates import TemplateFormatter
from taos.core.semantic.intent_classifier import IntentType
from taos.core.performance.latency import (
    LatencyOptimizer,
    ResponseCache,
    EarlyTerminator,
    LatencyStats,
)
from taos.core.performance.progress import (
    ProgressTracker,
    ProgressPhase,
    create_tracker,
    get_tracker,
    remove_tracker,
)


# ═══════════════════════════════════════════════════════════
# UPGRADE #1: TEMPLATE FORMATTER
# ═══════════════════════════════════════════════════════════

class TestTemplateFormatter:
    @pytest.fixture
    def fmt(self):
        return TemplateFormatter()

    def test_simple_lookup_format(self, fmt):
        result = fmt.format(
            intent=IntentType.SIMPLE_LOOKUP,
            answer="Python 3.12.1",
            confidence="High (95%)",
        )
        assert "👉 Python 3.12.1" in result
        assert "Confidence:" in result

    def test_definition_format(self, fmt):
        result = fmt.format(
            intent=IntentType.DEFINITION,
            answer="REST API is a web architecture style.",
            context="REST stands for Representational State Transfer.",
            confidence="High (90%)",
        )
        assert "👉" in result
        assert "Context:" in result

    def test_comparison_format(self, fmt):
        result = fmt.format(
            intent=IntentType.COMPARISON,
            answer="React is more popular, Vue is simpler.",
            key_points=["React has larger ecosystem", "Vue is easier to learn"],
            confidence="Medium (75%)",
        )
        assert "👉" in result
        assert "Key Differences:" in result

    def test_task_format_with_steps(self, fmt):
        result = fmt.format(
            intent=IntentType.TASK,
            answer="Fix the module error.",
            steps=["Install the missing module", "Restart the server"],
            commands=["pip install module-name"],
            confidence="High (85%)",
        )
        assert "👉" in result
        assert "Steps:" in result
        assert "pip install" in result

    def test_news_format(self, fmt):
        result = fmt.format(
            intent=IntentType.NEWS,
            answer="GPT-5 announced.",
            sources=["https://openai.com"],
            confidence="Medium (70%)",
        )
        assert "👉" in result
        assert "Sources:" in result

    def test_research_format(self, fmt):
        result = fmt.format(
            intent=IntentType.RESEARCH,
            answer="Transformer architectures have evolved significantly.",
            key_points=["Attention is all you need", "Multi-head attention"],
            sources=["https://arxiv.org/paper"],
            confidence="High (90%)",
        )
        assert "Key Findings:" in result

    def test_transform_format(self, fmt):
        result = fmt.format(
            intent=IntentType.TRANSFORM,
            answer="Here is the shortened version of the text.",
            confidence="High (95%)",
        )
        assert "👉" in result
        assert len(result.split("\n")) <= 5  # Should be compact


# ═══════════════════════════════════════════════════════════
# UPGRADE #2: LATENCY OPTIMIZER
# ═══════════════════════════════════════════════════════════

class TestResponseCache:
    def test_put_and_get(self):
        cache = ResponseCache(capacity=10)
        cache.put("test query", {"answer": "42"})
        result = cache.get("test query")
        assert result is not None
        assert result["answer"] == "42"

    def test_miss(self):
        cache = ResponseCache()
        assert cache.get("nonexistent") is None

    def test_ttl_expiry(self):
        cache = ResponseCache(ttl_seconds=0)
        cache.put("test", {"data": "old"})
        time.sleep(0.01)
        assert cache.get("test") is None

    def test_lru_eviction(self):
        cache = ResponseCache(capacity=2)
        cache.put("a", {"a": 1})
        cache.put("b", {"b": 2})
        cache.put("c", {"c": 3})
        assert cache.size <= 2

    def test_normalization(self):
        cache = ResponseCache()
        cache.put("  Hello World  ", {"data": 1})
        assert cache.get("hello world") is not None


class TestEarlyTerminator:
    def test_no_early_termination_at_start(self):
        et = EarlyTerminator()
        should, _ = et.should_terminate_early(0, 5, 0.95, True)
        assert should is False

    def test_early_termination_high_confidence(self):
        et = EarlyTerminator(min_confidence=0.9, min_steps=1)
        should, reason = et.should_terminate_early(1, 5, 0.95, True)
        assert should is True
        assert "High confidence" in reason

    def test_no_early_without_result(self):
        et = EarlyTerminator()
        should, _ = et.should_terminate_early(3, 5, 0.95, False)
        assert should is False

    def test_halfway_good_confidence(self):
        et = EarlyTerminator()
        should, _ = et.should_terminate_early(3, 5, 0.85, True)
        assert should is True


class TestLatencyStats:
    def test_record(self):
        stats = LatencyStats()
        stats.record(100)
        stats.record(200)
        assert stats.total_requests == 2
        assert stats.avg_latency_ms == 150

    def test_cache_hit_rate(self):
        stats = LatencyStats()
        stats.record(100, was_cache_hit=True)
        stats.record(200, was_cache_hit=False)
        assert stats.cache_hit_rate == 0.5


class TestLatencyOptimizer:
    def test_full_flow(self):
        opt = LatencyOptimizer()
        assert opt.check_cache("test") is None
        opt.cache_response("test", {"success": True, "answer": "ok"})
        result = opt.check_cache("test")
        assert result is not None
        assert result["answer"] == "ok"

    def test_stats(self):
        opt = LatencyOptimizer()
        opt.record_latency(150)
        stats = opt.get_stats()
        assert stats["total_requests"] == 1


# ═══════════════════════════════════════════════════════════
# UPGRADE #5: PROGRESS TRACKER
# ═══════════════════════════════════════════════════════════

class TestProgressTracker:
    def test_initial_state(self):
        tracker = ProgressTracker(request_id="test")
        assert tracker.current.progress_pct == 0
        assert tracker.is_complete is False

    def test_update_phases(self):
        tracker = ProgressTracker()
        tracker.update(ProgressPhase.RECEIVED)
        tracker.update(ProgressPhase.CLASSIFYING)
        tracker.update(ProgressPhase.PLANNING)
        assert len(tracker.history) == 3
        assert tracker.current.phase == ProgressPhase.PLANNING

    def test_executing_progress(self):
        tracker = ProgressTracker()
        update = tracker.update(ProgressPhase.EXECUTING, step=2, total_steps=5)
        assert update.progress_pct > 20
        assert "3 of 5" in update.label

    def test_complete(self):
        tracker = ProgressTracker()
        tracker.update(ProgressPhase.COMPLETE)
        assert tracker.is_complete is True

    def test_failed(self):
        tracker = ProgressTracker()
        tracker.update(ProgressPhase.FAILED, detail="timeout")
        assert tracker.is_complete is True

    def test_elapsed_time(self):
        tracker = ProgressTracker()
        time.sleep(0.01)
        assert tracker.elapsed_ms > 0

    def test_callback(self):
        tracker = ProgressTracker()
        updates = []
        tracker.on_update(lambda u: updates.append(u))
        tracker.update(ProgressPhase.CLASSIFYING)
        assert len(updates) == 1

    def test_history_dict(self):
        tracker = ProgressTracker()
        tracker.update(ProgressPhase.RECEIVED, "test query")
        history = tracker.history
        assert len(history) == 1
        assert history[0]["phase"] == "received"


class TestProgressStore:
    def test_create_and_get(self):
        tracker = create_tracker("test-123")
        found = get_tracker("test-123")
        assert found is tracker
        remove_tracker("test-123")

    def test_remove(self):
        create_tracker("test-456")
        remove_tracker("test-456")
        assert get_tracker("test-456") is None
