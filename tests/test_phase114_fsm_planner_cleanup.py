from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from taos.apps.api.routes.agent import _build_public_trace_summary
from taos.config.constants import FSMState
from taos.core.state.state_schema import GlobalState, PlanObject, PlanStep
from taos.orchestration.engine import OrchestrationEngine
from taos.orchestration.finalization_pipeline import FinalizationPipeline
from taos.orchestration.fsm_execution_loop import FSMExecutionLoop
from taos.orchestration.persistence_coordinator import PersistenceCoordinator
from taos.orchestration.planner_orchestrator import PlannerOrchestrator
from taos.orchestration.reflection_manager import ReflectionManager
from taos.orchestration.route_dispatcher import RouteDispatcher
from taos.scripts.run_full_live_qa_matrix import load_cases, run_matrix


class _FakeMemory:
    def build_context_window(self, state):
        return ["memory-context"]


class _FakeController:
    def set_plan(self, state, plan):
        return state.model_copy(update={"plan": plan, "current_fsm_state": FSMState.PLAN_READY})

    def transition(self, state, to_state, **kwargs):
        return state.model_copy(
            update={
                "current_fsm_state": to_state,
                "status": kwargs.get("status_update") or state.status,
                "error": kwargs.get("error_update"),
            }
        )

    def fail(self, state, reason):
        return state.model_copy(update={"current_fsm_state": FSMState.TERMINATING, "status": "failed", "error": reason})


class _FakePlannerEngine:
    def __init__(self):
        self._memory = _FakeMemory()
        self._decomposer = SimpleNamespace(
            analyze=lambda **_: SimpleNamespace(should_decompose=False, reason="", subgoals=[]),
        )
        self._settings = SimpleNamespace(goal_decomposition_enabled=False, max_steps=4, cost_budget_per_task=1.0)
        self._plan_validator = SimpleNamespace(validate=lambda plan: SimpleNamespace(is_valid=True, errors=[]))
        self._controller = _FakeController()
        self.logged = []

    def _is_freshness_sensitive_research(self, goal):
        return False

    def _log(self, event, **kwargs):
        self.logged.append((event, kwargs))

    async def _generate_plan_with_adapter(self, **kwargs):
        return PlanObject(steps=[PlanStep(id="s1", action="Answer directly")]), 0.12


class _FakeLoopGuard:
    def record_state(self, state):
        self.last_state = state

    def check_all(self, state):
        return None


class _FakeFSMEngine:
    def __init__(self):
        self._settings = SimpleNamespace(max_steps=5)
        self._request_started_at = 0.0
        self._request_deadline_seconds = 30.0
        self._termination_checker = SimpleNamespace(check=lambda state: None)
        self._controller = _FakeController()
        self._loop_guard = _FakeLoopGuard()
        self.logged = []

    def _log(self, event, **kwargs):
        self.logged.append((event, kwargs))


@pytest.mark.asyncio
async def test_planner_orchestrator_returns_validated_plan_metadata():
    state = GlobalState(goal="make a plan", current_fsm_state=FSMState.PLANNING)
    engine = _FakePlannerEngine()

    new_state, plan, metadata = await PlannerOrchestrator().generate_and_validate(
        engine=engine,
        state=state,
        goal="make a plan",
        intent="task",
        feedback_hints=["prefer concise"],
        planner_hints=["use safe tools"],
    )

    assert plan.step_count == 1
    assert new_state.plan == plan
    assert metadata.planning_cost == 0.12
    assert metadata.feedback_hints_count == 1
    assert metadata.planner_hints_count == 1
    assert metadata.validation_errors == []


@pytest.mark.asyncio
async def test_fsm_execution_loop_preserves_success_termination_behavior():
    state = GlobalState(
        goal="already done",
        current_fsm_state=FSMState.EXECUTING,
        plan=PlanObject(steps=[]),
        status="running",
    )

    final_state = await FSMExecutionLoop().run(engine=_FakeFSMEngine(), state=state)

    assert final_state.current_fsm_state == FSMState.TERMINATING
    assert final_state.status == "success"


def test_reflection_manager_preserves_replan_and_terminate_mapping():
    manager = ReflectionManager()

    replan = manager.decision_for_state(GlobalState(current_fsm_state=FSMState.REPLANNING))
    terminate = manager.decision_for_state(GlobalState(current_fsm_state=FSMState.TERMINATING))

    assert replan.should_replan is True
    assert replan.should_terminate is False
    assert terminate.should_terminate is True


@pytest.mark.asyncio
async def test_finalization_pipeline_preserves_response_contract_fields():
    class Engine:
        async def _finalize_legacy(self, **kwargs):
            return {
                "request_id": "r1",
                "result": "Answer\nDone.",
                "formatted_response": "Answer\nDone.",
                "trace": {"public_summary": {"route": {"label": "task"}}},
                "warnings": [],
            }

    result = await FinalizationPipeline().finalize(engine=Engine(), state=None)
    metadata = FinalizationPipeline().metadata_for(result)

    assert result["request_id"] == "r1"
    assert result["formatted_response"].startswith("Answer")
    assert metadata.public_trace_present is True
    assert "result" in metadata.response_keys


@pytest.mark.asyncio
async def test_persistence_coordinator_treats_writes_as_best_effort():
    class Engine:
        def __init__(self):
            self.logged = []

        async def _persist_execution_memory_legacy(self, **kwargs):
            raise RuntimeError("persistence unavailable")

        def _log(self, event, **kwargs):
            self.logged.append((event, kwargs))

    outcome = await PersistenceCoordinator().persist_execution_memory(
        engine=Engine(),
        user_id="u1",
        state=GlobalState(goal="x"),
        result={"status": "success"},
        latency_ms=1.0,
    )

    assert outcome.attempted is True
    assert outcome.success is False
    assert "persistence unavailable" in str(outcome.error)


def test_engine_uses_phase114_module_boundaries_for_task_path():
    assert "_planner_orchestrator.generate_and_validate" in inspect.getsource(
        OrchestrationEngine._generate_and_validate_plan
    )
    assert "_fsm_execution_loop.run" in inspect.getsource(OrchestrationEngine._execution_loop)
    assert "_finalization_pipeline.finalize" in inspect.getsource(OrchestrationEngine._finalize)
    assert "_persistence_coordinator.persist_execution_memory" in inspect.getsource(
        OrchestrationEngine._persist_execution_memory
    )


def test_fast_search_and_research_routes_do_not_enter_planner_fsm_owner():
    dispatcher = RouteDispatcher()

    assert dispatcher.owner_for_route("fast_search") == "search_lite"
    assert dispatcher.owner_for_route("deep_search") == "research_pipeline"
    assert dispatcher.owner_for_route("news_search") == "research_pipeline"
    assert dispatcher.owner_for_route("official_search") == "research_pipeline"
    assert dispatcher.owner_for_route("comparison_search") == "research_pipeline"
    assert dispatcher.owner_for_route("task") == "fsm_planner"


def test_public_trace_shape_remains_stable():
    summary = _build_public_trace_summary(
        {
            "route_label": "deep_search",
            "route_decision": {"route": "deep_search", "confidence": 0.86, "reason": "research"},
            "route_boundary_summary": {"route": "deep_search", "owner": "research_pipeline", "will_use_planner": False},
            "timing": {"total_ms": 12000, "extract_ms": 6000},
            "trust_block": {"trust_level": "medium", "answer_mode": "best_supported", "unsupported_claims": 0},
            "evidence_stats": {"sources_found": 8, "sources_used": 3, "coverage": 0.82},
        }
    )

    assert set(summary.keys()) == {"route", "execution", "research", "trust", "latency"}
    assert summary["route"]["owner"] == "research_pipeline"
    assert summary["execution"]["planner_used"] is False
    assert summary["latency"]["total_ms"] == 12000


def test_phase113_mock_qa_matrix_still_passes():
    report = run_matrix(cases=load_cases(Path("qa/live_qa_cases.json")), live=False)

    assert report["failed_count"] == 0
    assert report["passed_count"] == 8
