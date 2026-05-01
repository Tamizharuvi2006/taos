"""AgentRouter selects the best specialized agent for each execution step."""

from __future__ import annotations

from taos.core.agents.base_agent import BaseAgent
from taos.core.agents.reputation import AgentReputationStore
from taos.core.state.state_schema import GlobalState, PlanStep, StepType


class AgentRouter:
    """
    Minimal routing layer for TAOS v2.

    Routing behavior:
    - `web_search` steps route to ResearchAgent.
    - Analysis/comparison actions can route to PlannerAgent.
    - Reflecting state can route to CriticAgent.
    - All other executable steps route to ExecutionAgent.
    - Additional specialized routes can be introduced incrementally.
    """

    def __init__(
        self,
        execution_agent: BaseAgent,
        research_agent: BaseAgent | None = None,
        planner_agent: BaseAgent | None = None,
        critic_agent: BaseAgent | None = None,
        reputation_store: AgentReputationStore | None = None,
    ) -> None:
        self._execution_agent = execution_agent
        self._research_agent = research_agent or execution_agent
        self._planner_agent = planner_agent or execution_agent
        self._critic_agent = critic_agent or execution_agent
        self._reputation = reputation_store

    def select(self, step: PlanStep, state: GlobalState) -> BaseAgent:
        """
        Select an agent for a given step.

        Current rule-set is intentionally simple and deterministic.
        """
        action = (step.action or "").lower()
        candidates: list[BaseAgent]

        if step.step_type == StepType.DAG_EXEC:
            # DAG execution is a controlled executor concern under FSM.
            return self._execution_agent
        if step.tool == "web_search":
            candidates = [self._research_agent, self._execution_agent]
            return self._pick_by_trust(candidates)
        if "analyze" in action or "compare" in action:
            candidates = [self._planner_agent, self._research_agent, self._execution_agent]
            return self._pick_by_trust(candidates)
        if state.current_fsm_state == "REFLECTING":
            return self._critic_agent
        candidates = [self._execution_agent, self._planner_agent]
        return self._pick_by_trust(candidates)

    def _pick_by_trust(self, candidates: list[BaseAgent]) -> BaseAgent:
        """Pick highest-trust candidate; preserve order for ties/no data."""
        if not candidates:
            raise ValueError("AgentRouter requires at least one candidate")
        if not self._reputation:
            return candidates[0]

        best = candidates[0]
        best_score = self._reputation.trust(best.name)
        for agent in candidates[1:]:
            score = self._reputation.trust(agent.name)
            if score > best_score:
                best = agent
                best_score = score
        return best
