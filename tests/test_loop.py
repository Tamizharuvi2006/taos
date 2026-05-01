"""
TAOS Tests — Loop Detection, Memory, and State Tests.

Tests the loop guard, memory system, confidence scorer,
and state management.
"""

from __future__ import annotations

import time
import pytest

from taos.config.constants import FSMState
from taos.core.loop.loop_guard import LoopGuard
from taos.core.loop.termination import TerminationChecker, build_final_result
from taos.core.memory.memory_store import MemoryStore, MemoryEntry
from taos.core.memory.memory_manager import MemoryManager
from taos.core.memory.retrieval import MemoryRetriever
from taos.core.reflection.confidence import ConfidenceScorer
from taos.core.state.state_schema import (
    GlobalState,
    PlanObject,
    PlanStep,
    StepResult,
    ReflectionResult,
    StateDelta,
)
from taos.core.state.state_manager import StateManager
from taos.core.state.state_diff import compute_diff


# ═══════════════════════════════════════════════════════════
# LOOP GUARD TESTS
# ═══════════════════════════════════════════════════════════

class TestLoopGuard:
    @pytest.fixture
    def guard(self):
        return LoopGuard()

    def test_no_loop_initially(self, guard):
        state = GlobalState(goal="test")
        assert guard.check_all(state) is None

    def test_repeated_actions_detected(self, guard):
        state = GlobalState(goal="test")
        for _ in range(4):
            guard.record_action("same action")
        reason = guard.check_all(state)
        assert reason is not None
        assert "repeated" in reason.lower() or "same action" in reason.lower()

    def test_repeated_errors_detected(self, guard):
        state = GlobalState(goal="test")
        for _ in range(3):
            guard.record_error("same error")
        reason = guard.check_all(state)
        assert reason is not None

    def test_step_limit_detected(self, guard):
        state = GlobalState(goal="test", step=15)
        reason = guard.check_all(state)
        assert reason is not None
        assert "limit" in reason.lower()

    def test_reset_clears_history(self, guard):
        state = GlobalState(goal="test")
        for _ in range(4):
            guard.record_action("same action")
        guard.reset()
        assert guard.check_all(state) is None


# ═══════════════════════════════════════════════════════════
# TERMINATION CHECKER TESTS
# ═══════════════════════════════════════════════════════════

class TestTerminationChecker:
    @pytest.fixture
    def checker(self):
        return TerminationChecker()

    def test_no_termination_on_fresh_state(self, checker):
        state = GlobalState(goal="test")
        assert checker.check(state) is None

    def test_all_steps_completed(self, checker):
        plan = PlanObject(steps=[PlanStep(id="s1", action="Do")])
        state = GlobalState(goal="test", plan=plan, step=1, status="running")
        decision = checker.check(state)
        assert decision is not None
        assert decision.should_terminate is True
        assert decision.final_status == "success"

    def test_low_confidence_terminates(self, checker):
        state = GlobalState(goal="test", confidence=0.1)
        decision = checker.check(state)
        assert decision is not None
        assert decision.final_status == "failed"

    def test_cost_exceeded(self, checker):
        state = GlobalState(goal="test", cost=10.0)
        decision = checker.check(state)
        assert decision is not None
        assert decision.final_status == "failed"


# ═══════════════════════════════════════════════════════════
# MEMORY STORE TESTS
# ═══════════════════════════════════════════════════════════

class TestMemoryStore:
    @pytest.fixture
    def store(self):
        return MemoryStore(capacity=10)

    def test_put_and_get(self, store):
        store.put("key1", "value1")
        entry = store.get("key1")
        assert entry is not None
        assert entry.value == "value1"

    def test_get_missing_key(self, store):
        assert store.get("nonexistent") is None

    def test_low_confidence_rejected(self, store):
        result = store.put("key1", "value1", confidence=0.3)
        assert result is False
        assert store.get("key1") is None

    def test_overwrite_existing(self, store):
        store.put("key1", "value1")
        store.put("key1", "value2")
        assert store.get_value("key1") == "value2"

    def test_capacity_eviction(self):
        store = MemoryStore(capacity=3)
        store.put("k1", "v1")
        store.put("k2", "v2")
        store.put("k3", "v3")
        store.put("k4", "v4")
        assert store.size <= 3

    def test_tag_indexing(self, store):
        store.put("k1", "v1", tags={"tag_a", "tag_b"})
        store.put("k2", "v2", tags={"tag_a"})
        results = store.get_by_tag("tag_a")
        assert len(results) == 2

    def test_delete(self, store):
        store.put("k1", "v1")
        assert store.delete("k1") is True
        assert store.get("k1") is None

    def test_clear(self, store):
        store.put("k1", "v1")
        store.put("k2", "v2")
        store.clear()
        assert store.size == 0

    def test_stats(self, store):
        store.put("k1", "v1")
        store.get("k1")
        store.get("nonexistent")
        stats = store.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["writes"] == 1

    def test_get_recent(self, store):
        store.put("k1", "v1")
        store.put("k2", "v2")
        store.put("k3", "v3")
        recent = store.get_recent(2)
        assert len(recent) == 2


# ═══════════════════════════════════════════════════════════
# MEMORY MANAGER TESTS
# ═══════════════════════════════════════════════════════════

class TestMemoryManager:
    @pytest.fixture
    def manager(self):
        return MemoryManager()

    def test_store_step_result(self, manager):
        step = PlanStep(id="s1", action="Search", tool="web_search")
        result = StepResult(step_id="s1", result="Found data", success=True)
        key = manager.store_step_result("s1", step, result)
        assert key == "step_result:s1"
        assert manager.store.get(key) is not None

    def test_store_reflection(self, manager):
        reflection = ReflectionResult(success=True, confidence=0.85, reasoning="Good result")
        key = manager.store_reflection("s1", reflection)
        assert "reflection" in key

    def test_build_context_window(self, manager):
        state = GlobalState(goal="Test goal")
        context = manager.build_context_window(state)
        assert len(context) > 0
        assert any("Test goal" in c for c in context)

    def test_reset(self, manager):
        manager.store_context("test_key", "test_value")
        manager.reset()
        assert manager.store.size == 0


# ═══════════════════════════════════════════════════════════
# MEMORY RETRIEVER TESTS
# ═══════════════════════════════════════════════════════════

class TestMemoryRetriever:
    @pytest.fixture
    def store_with_data(self):
        store = MemoryStore(capacity=50, min_confidence=0.3)
        store.put("k1", "v1", confidence=0.9, tags={"step_result"})
        store.put("k2", "v2", confidence=0.5, tags={"step_result", "failed"})
        store.put("k3", "v3", confidence=0.8, tags={"reflection"})
        return store

    def test_retrieve_top_k(self, store_with_data):
        retriever = MemoryRetriever(store_with_data)
        results = retriever.retrieve_top_k(k=2)
        assert len(results) <= 2
        # Should be sorted by score descending
        if len(results) == 2:
            assert results[0].score >= results[1].score

    def test_retrieve_with_tag_filter(self, store_with_data):
        retriever = MemoryRetriever(store_with_data)
        results = retriever.retrieve_top_k(k=10, tags={"failed"})
        assert len(results) == 1

    def test_retrieve_failures(self, store_with_data):
        retriever = MemoryRetriever(store_with_data)
        results = retriever.retrieve_failures()
        assert len(results) == 1


# ═══════════════════════════════════════════════════════════
# CONFIDENCE SCORER TESTS
# ═══════════════════════════════════════════════════════════

class TestConfidenceScorer:
    @pytest.fixture
    def scorer(self):
        return ConfidenceScorer()

    def test_initial_rolling_average(self, scorer):
        assert scorer.get_rolling_average() == 1.0

    def test_rolling_average(self, scorer):
        scorer.record(0.8)
        scorer.record(0.6)
        scorer.record(0.7)
        avg = scorer.get_rolling_average(3)
        assert abs(avg - 0.7) < 0.01

    def test_trend_improving(self, scorer):
        scorer.record(0.3)
        scorer.record(0.5)
        scorer.record(0.8)
        assert scorer.get_trend() == "improving"

    def test_trend_degrading(self, scorer):
        scorer.record(0.8)
        scorer.record(0.5)
        scorer.record(0.3)
        assert scorer.get_trend() == "degrading"

    def test_reset(self, scorer):
        scorer.record(0.5)
        scorer.reset()
        assert len(scorer.history) == 0


# ═══════════════════════════════════════════════════════════
# STATE MANAGEMENT TESTS
# ═══════════════════════════════════════════════════════════

class TestStateManager:
    @pytest.fixture
    def manager(self):
        return StateManager()

    def test_create_initial_state(self, manager):
        state = manager.create_initial_state("Test goal")
        assert state.goal == "Test goal"
        assert state.current_fsm_state == FSMState.INIT
        assert state.state_version == 0

    def test_apply_delta_increments_version(self, manager):
        state = manager.create_initial_state("Test")
        delta = StateDelta(fsm_state_update=FSMState.PLANNING)
        new_state = manager.apply_delta(state, delta)
        assert new_state.state_version == 1

    def test_state_history_preserved(self, manager):
        state = manager.create_initial_state("Test")
        delta = StateDelta(fsm_state_update=FSMState.PLANNING)
        manager.apply_delta(state, delta)
        history = manager.get_history()
        assert len(history) >= 1

    def test_hash_chaining(self, manager):
        state = manager.create_initial_state("Test")
        hash1 = state.compute_hash()
        delta = StateDelta(fsm_state_update=FSMState.PLANNING)
        new_state = manager.apply_delta(state, delta)
        assert new_state.prev_state_hash == hash1


class TestStateDiff:
    def test_diff_detects_changes(self):
        state1 = GlobalState(goal="test", step=0, confidence=1.0)
        state2 = state1.model_copy(update={"step": 1, "confidence": 0.85})
        diff = compute_diff(state1, state2)
        assert len(diff.changed_fields) > 0

    def test_diff_no_changes(self):
        state = GlobalState(goal="test")
        diff = compute_diff(state, state)
        assert len(diff.changed_fields) == 0


class TestBuildFinalResult:
    def test_build_final_result(self):
        state = GlobalState(
            goal="Test goal",
            status="success",
            step=2,
            cost=0.005,
            confidence=0.9,
        )
        result = build_final_result(state)
        assert result["goal"] == "Test goal"
        assert result["success"] is True
        assert result["steps_executed"] == 2
        assert result["total_cost"] == 0.005
