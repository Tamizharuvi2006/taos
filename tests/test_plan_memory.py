from __future__ import annotations

from taos.core.planner.plan_memory import PlanMemoryStore
from taos.core.state.state_schema import PlanObject, PlanStep


def _plan(*steps: str) -> PlanObject:
    return PlanObject(
        steps=[PlanStep(action=s, tool="web_search") for s in steps],
    )


def test_plan_memory_add_and_retrieve_similar():
    store = PlanMemoryStore()
    store.add_record(
        goal="Compare GPT-4 and Claude performance",
        plan=_plan("search benchmark data", "summarize differences"),
        outcome="success",
        confidence=0.85,
        cost=0.12,
        latency_ms=1800.0,
    )
    store.add_record(
        goal="Fix syntax error in python script",
        plan=_plan("inspect traceback", "apply fix"),
        outcome="success",
        confidence=0.9,
        cost=0.05,
        latency_ms=900.0,
    )

    matches = store.retrieve_similar("Compare Claude and GPT-4")
    assert len(matches) >= 1
    assert "Compare GPT-4" in matches[0].goal


def test_plan_memory_builds_hints_for_success_and_failure():
    store = PlanMemoryStore()
    store.add_record(
        goal="Latest AI model updates",
        plan=_plan("search latest releases", "cross-validate sources"),
        outcome="failed",
        confidence=0.4,
        cost=0.2,
        latency_ms=2500.0,
        failure_reason="outdated source",
    )
    records = store.retrieve_similar("latest ai models", top_k=2, min_similarity=0.0)
    hints = store.build_planner_hints(records, max_hints=2)

    assert len(hints) >= 1
    assert any("failed pattern" in h.lower() or "successful pattern" in h.lower() for h in hints)
