from __future__ import annotations

from taos.core.tools.tool_learning import ToolLearningStore


def test_tool_learning_records_and_scores():
    store = ToolLearningStore()
    store.record("web_search", success=True, latency=120.0, cost=0.01, confidence=0.8)
    store.record("web_search", success=False, latency=200.0, cost=0.02, confidence=0.2)

    stats = store.get_stats("web_search")
    assert stats.calls == 2
    assert stats.success == 1
    assert stats.failures == 1
    assert 0.0 <= stats.success_rate <= 1.0


def test_tool_learning_avoid_after_consistent_failures():
    store = ToolLearningStore()
    for _ in range(6):
        store.record("web_search", success=False, latency=350.0, cost=0.01, confidence=0.1)

    assert store.should_avoid("web_search") is True
    assert store.suggest_fallback("web_search") == "http_request"


def test_tool_learning_snapshot_contains_expected_fields():
    store = ToolLearningStore()
    store.record("http_request", success=True, latency=90.0, cost=0.005, confidence=0.9)

    snap = store.snapshot()
    assert "http_request" in snap
    assert "success_rate" in snap["http_request"]
    assert "avg_latency" in snap["http_request"]
