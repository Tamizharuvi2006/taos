from __future__ import annotations

from taos.core.agents.agent_router import AgentRouter
from taos.core.agents.reputation import AgentReputationStore
from taos.core.state.state_schema import GlobalState, PlanStep


class _NamedAgent:
    def __init__(self, agent_name: str):
        self.name = agent_name


def test_agent_reputation_score_improves_with_success():
    store = AgentReputationStore()
    store.record("research_agent", success=True, confidence=0.9, latency_ms=200)
    store.record("research_agent", success=True, confidence=0.8, latency_ms=250)
    store.record("research_agent", success=False, confidence=0.2, latency_ms=900)

    score = store.trust("research_agent")
    assert 0.0 <= score <= 1.0


def test_router_prefers_higher_trust_candidate():
    rep = AgentReputationStore()
    for _ in range(5):
        rep.record("research_agent", success=True, confidence=0.9, latency_ms=200)
        rep.record("execution_agent", success=False, confidence=0.2, latency_ms=1400)

    router = AgentRouter(
        execution_agent=_NamedAgent("execution_agent"),  # type: ignore[arg-type]
        research_agent=_NamedAgent("research_agent"),  # type: ignore[arg-type]
        planner_agent=_NamedAgent("planner_agent"),  # type: ignore[arg-type]
        reputation_store=rep,
    )
    step = PlanStep(action="search latest updates", tool="web_search", tool_input={"query": "ai"})
    state = GlobalState(goal="latest updates")

    selected = router.select(step, state)
    assert selected.name == "research_agent"
