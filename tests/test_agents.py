from __future__ import annotations

import pytest

from taos.core.agents.agent_router import AgentRouter
from taos.core.agents.critic_agent import CriticAgent
from taos.core.agents.execution_agent import ExecutionAgent
from taos.core.agents.messages import AgentMessage, MessagePriority, MessageType
from taos.core.agents.research_agent import ResearchAgent
from taos.core.state.state_schema import GlobalState, PlanStep, StepResult
from taos.core.tools.source_ranker import SourceRanker


class _DummyExecutor:
    async def execute_step(self, step: PlanStep, state: GlobalState, step_index: int) -> StepResult:
        _ = state
        _ = step_index
        if step.tool == "web_search":
            return StepResult(
                step_id=step.id,
                success=True,
                tool_name="web_search",
                result={
                    "results": [
                        {
                            "title": "Python Docs",
                            "link": "https://docs.python.org/3/",
                            "snippet": "Official Python documentation and language reference.",
                        },
                        {
                            "title": "Random Blog",
                            "link": "https://example.com/post",
                            "snippet": "General tips.",
                        },
                    ]
                },
            )
        return StepResult(step_id=step.id, success=True, result="ok")


class _NamedAgent:
    def __init__(self, agent_name: str):
        self.name = agent_name


@pytest.mark.asyncio
async def test_router_routes_web_search_to_research_agent():
    executor = _DummyExecutor()
    execution_agent = ExecutionAgent(executor)  # type: ignore[arg-type]
    research_agent = ResearchAgent(executor, SourceRanker())  # type: ignore[arg-type]
    router = AgentRouter(execution_agent=execution_agent, research_agent=research_agent)

    state = GlobalState(goal="find docs")
    search_step = PlanStep(action="search", tool="web_search", tool_input={"query": "python docs"})
    normal_step = PlanStep(action="summarize")

    assert router.select(search_step, state).name == "research_agent"
    assert router.select(normal_step, state).name == "execution_agent"


def test_router_routes_compare_action_to_planner_agent():
    execution_agent = _NamedAgent("execution_agent")
    research_agent = _NamedAgent("research_agent")
    planner_agent = _NamedAgent("planner_agent")
    router = AgentRouter(
        execution_agent=execution_agent,  # type: ignore[arg-type]
        research_agent=research_agent,  # type: ignore[arg-type]
        planner_agent=planner_agent,  # type: ignore[arg-type]
    )

    state = GlobalState(goal="compare models")
    compare_step = PlanStep(action="Compare GPT-4 and Claude outputs")

    assert router.select(compare_step, state).name == "planner_agent"


@pytest.mark.asyncio
async def test_research_agent_enriches_ranked_results():
    executor = _DummyExecutor()
    agent = ResearchAgent(executor, SourceRanker())  # type: ignore[arg-type]
    state = GlobalState(goal="search")
    step = PlanStep(action="search", tool="web_search", tool_input={"query": "python docs"})

    result = await agent.execute(step, state, 0)

    assert result.success is True
    assert isinstance(result.result, dict)
    assert "ranked_results" in result.result
    assert "research_summary" in result.result
    assert len(result.result["ranked_results"]) >= 1


def test_critic_agent_marks_failed_steps_invalid():
    critic = CriticAgent()
    state = GlobalState(goal="test")
    step = PlanStep(action="do something")
    failed = StepResult(step_id=step.id, success=False, error="tool crashed")

    critique = critic.critique(step=step, step_result=failed, state=state)

    assert critique["valid"] is False
    assert critique["confidence"] == 0.0
    assert "tool crashed" in critique["issues"][0]


def test_critic_agent_precheck_rejects_invalid_tool_step():
    critic = CriticAgent()
    state = GlobalState(goal="test")
    bad_step = PlanStep(action="call tool", tool="http_request", tool_input=None)

    pre = critic.pre_check(bad_step, state)

    assert pre["allowed"] is False
    assert any("tool_input" in issue for issue in pre["issues"])


def test_parallel_batch_safety_validator_rejects_dependency_chain():
    from taos.orchestration.engine import OrchestrationEngine

    engine = OrchestrationEngine()
    s1 = PlanStep(id="s1", action="search a", tool="web_search", tool_input={"query": "a"})
    s2 = PlanStep(id="s2", action="search b", tool="web_search", tool_input={"query": "b"}, depends_on=["s1"])

    assert engine._validate_parallel_batch_safety([s1, s2]) is False


def test_message_influence_forces_research_after_low_conf_infos():
    from taos.orchestration.engine import OrchestrationEngine

    engine = OrchestrationEngine()
    state = GlobalState(goal="compare models", step=1)
    engine._message_store.emit(
        AgentMessage(
            agent_name="research_agent",
            step_id="a",
            message_type=MessageType.INFO,
            priority=MessagePriority.MEDIUM,
            content="conflicting data 1",
            confidence=0.5,
            created_step_index=1,
        ),
        current_step_index=1,
    )
    engine._message_store.emit(
        AgentMessage(
            agent_name="research_agent",
            step_id="b",
            message_type=MessageType.INFO,
            priority=MessagePriority.MEDIUM,
            content="conflicting data 2",
            confidence=0.4,
            created_step_index=1,
        ),
        current_step_index=1,
    )
    influence = engine._derive_message_influence(state)
    assert influence["force_research"] is True


def test_tool_learning_override_applies_fallback():
    from taos.orchestration.engine import OrchestrationEngine

    engine = OrchestrationEngine()
    for _ in range(6):
        engine._tool_learning.record(
            tool_name="web_search",
            success=False,
            latency=420.0,
            cost=0.01,
            confidence=0.1,
        )
    step = PlanStep(action="search for latest release", tool="web_search", tool_input={"query": "release"})
    adjusted = engine._apply_tool_learning(step, current_step_index=0)
    assert adjusted.tool == "http_request"
