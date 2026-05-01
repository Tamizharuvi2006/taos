from __future__ import annotations

import time
from typing import Any

from taos.config.constants import ErrorType, FSMState
from taos.core.observability import get_metrics
from taos.core.performance.progress import ProgressPhase, get_tracker
from taos.core.state.state_schema import GlobalState, StepResult
from taos.core.agents import MessagePriority, MessageType


class FSMExecutionLoop:
    """Owns the PLAN_READY -> EXECUTING -> REFLECTING task loop."""

    async def run(self, *, engine: Any, state: GlobalState) -> GlobalState:
        max_steps = max(1, int(engine._settings.max_steps))
        while state.current_fsm_state == FSMState.EXECUTING:
            if (
                engine._request_started_at > 0
                and (time.time() - engine._request_started_at) >= engine._request_deadline_seconds
            ):
                engine._log("engine.request_time_budget_exceeded", max_seconds=engine._request_deadline_seconds)
                state = engine._controller.transition(
                    state,
                    FSMState.TERMINATING,
                    status_update="timeout",
                    error_update="TIME_BUDGET_EXCEEDED",
                )
                break
            if state.step >= max_steps:
                engine._log("engine.step_limit_reached", max_steps=max_steps)
                state = engine._controller.transition(state, FSMState.TERMINATING, status_update="success")
                break

            termination = engine._termination_checker.check(state)
            if termination:
                state = engine._controller.transition(
                    state,
                    FSMState.TERMINATING,
                    status_update=termination.final_status,
                    error_update=(termination.reason if termination.final_status != "success" else None),
                )
                break

            engine._loop_guard.record_state(state)
            loop_reason = engine._loop_guard.check_all(state)
            if loop_reason:
                state = engine._controller.fail(state, "Loop detected: " + loop_reason)
                break

            plan = state.plan
            if not plan or state.step >= len(plan.steps):
                state = engine._controller.transition(state, FSMState.TERMINATING, status_update="success")
                break

            remaining_budget = max(0, max_steps - state.step)
            parallel_batch = engine._get_parallel_search_batch(state=state, max_batch_size=remaining_budget)
            influence = engine._derive_message_influence(state)
            if influence["critical_warning"]:
                parallel_batch = []
            if len(parallel_batch) > 1 and not engine._validate_parallel_batch_safety(parallel_batch):
                parallel_batch = []
            if len(parallel_batch) > 1:
                engine._log(
                    "engine.parallel_batch_start",
                    size=len(parallel_batch),
                    step_ids=[s.id for s in parallel_batch],
                )
                batch_results = await engine._execute_parallel_search_batch(steps=parallel_batch, state=state)
                state, stop = await engine._process_step_results_in_order(
                    state=state,
                    ordered_steps=parallel_batch,
                    result_map=batch_results,
                )
                if stop:
                    break
                continue

            current_step = plan.steps[state.step]
            execution_step = engine._apply_tool_learning(current_step, current_step_index=state.step)
            influence = engine._derive_message_influence(state)
            pre_critique = engine._critic_agent.pre_check(execution_step, state)
            engine._log(
                "engine.critic_precheck",
                step_id=execution_step.id,
                allowed=pre_critique.get("allowed", True),
                issues=len(pre_critique.get("issues", [])),
            )
            if not pre_critique.get("allowed", True):
                engine._emit_agent_message(
                    agent_name="critic_agent",
                    step_id=execution_step.id,
                    message_type=MessageType.ERROR,
                    priority=MessagePriority.HIGH,
                    content="Pre-check rejected step before execution",
                    confidence=0.2,
                    current_step_index=state.step,
                )
                reason = ", ".join(pre_critique.get("issues", [])[:2]) or "pre-check rejected step"
                step_result = engine._critic_agent.enforce(
                    StepResult(
                        step_id=execution_step.id,
                        success=False,
                        error=f"Critic pre-check failed: {reason}",
                        error_type=ErrorType.VALIDATION_ERROR.value,
                        tool_name=execution_step.tool,
                    ),
                    {"valid": False, "issues": pre_critique.get("issues", []), "confidence": 0.0},
                )
                engine._memory.store_step_result(execution_step.id, execution_step, step_result)
                state = engine._controller.record_step_result(state, step_result)
                reflection = await engine._reflector.reflect(
                    step_result=step_result,
                    step=execution_step,
                    state=state,
                )
                engine._memory.store_reflection(execution_step.id, reflection)
                engine._confidence_scorer.record(reflection.confidence)
                engine._tool_learning.record(
                    tool_name=execution_step.tool,
                    success=False,
                    latency=0.0,
                    cost=float(step_result.cost or 0.0),
                    confidence=0.0,
                )
                state = engine._controller.handle_reflection(state, reflection)
                if state.current_fsm_state == FSMState.REPLANNING:
                    state = await engine._handle_replanning(state)
                decision = engine._reflection_manager.decision_for_state(state)
                if decision.should_terminate:
                    break
                continue

            selected_agent = engine._agent_router.select(execution_step, state)
            if influence["force_research"] and execution_step.tool is None:
                selected_agent = engine._research_agent
                engine._emit_agent_message(
                    agent_name="router",
                    step_id=execution_step.id,
                    message_type=MessageType.DECISION,
                    priority=MessagePriority.MEDIUM,
                    content="Rerouted step to research agent based on low-confidence signals",
                    confidence=0.7,
                    current_step_index=state.step,
                )
            get_metrics().agent_usage[selected_agent.name] += 1
            trust_score = engine._agent_reputation.trust(selected_agent.name)
            engine._log(
                "engine.agent_selected",
                step_id=execution_step.id,
                agent=selected_agent.name,
                tool=execution_step.tool,
                trust_score=round(trust_score, 4),
            )
            tracker = get_tracker(state.request_id)
            if tracker:
                tracker.update(
                    ProgressPhase.EXECUTING,
                    detail=f"AGENT_SELECTED:{selected_agent.name}",
                    step=state.step,
                    total_steps=len(plan.steps),
                )
            execution_started = time.time()
            step_result = await engine._execute_step_with_adapter(
                selected_agent_name=selected_agent.name,
                step=execution_step,
                state=state,
                step_index=state.step,
                request_id=state.request_id,
                fallback_agent=selected_agent,
            )
            step_result = await engine._recover_research_step_failure(
                step=execution_step,
                step_result=step_result,
                state=state,
            )
            execution_latency = (time.time() - execution_started) * 1000
            if selected_agent.name == "research_agent":
                result_payload = step_result.result if isinstance(step_result.result, dict) else {}
                ranked = result_payload.get("ranked_results", []) if isinstance(result_payload, dict) else []
                if ranked:
                    low_count = sum(1 for r in ranked if float(r.get("rank_score", 0.0) or 0.0) < 0.45)
                    if low_count >= 2:
                        engine._emit_agent_message(
                            agent_name="research_agent",
                            step_id=execution_step.id,
                            message_type=MessageType.INFO,
                            priority=MessagePriority.MEDIUM,
                            content="Found multiple low-confidence sources; verification recommended.",
                            confidence=0.5,
                            current_step_index=state.step,
                            targets=["critic_agent", "planner_agent"],
                        )
            if selected_agent.name == "execution_agent" and "compare" in (execution_step.action or "").lower():
                engine._emit_agent_message(
                    agent_name="execution_agent",
                    step_id=execution_step.id,
                    message_type=MessageType.REQUEST,
                    priority=MessagePriority.MEDIUM,
                    content="Need structured supporting research context for comparison output.",
                    confidence=0.7,
                    current_step_index=state.step,
                    targets=["research_agent"],
                )
            critique = engine._critic_agent.post_check(step=execution_step, step_result=step_result, state=state)
            engine._log(
                "engine.critic_result",
                step_id=execution_step.id,
                valid=critique.get("valid", True),
                confidence=critique.get("confidence", 0.0),
                issues=len(critique.get("issues", [])),
            )
            if not critique.get("valid", True):
                get_metrics().critic_failures += 1
                engine._emit_agent_message(
                    agent_name="critic_agent",
                    step_id=execution_step.id,
                    message_type=MessageType.WARNING,
                    priority=MessagePriority.HIGH
                    if float(critique.get("confidence", 0.0) or 0.0) < 0.5
                    else MessagePriority.MEDIUM,
                    content="Post-check flagged low-quality output",
                    confidence=float(critique.get("confidence", 0.0) or 0.0),
                    current_step_index=state.step,
                )
            fail_threshold = 0.5 if trust_score < float(engine._settings.agent_low_trust_threshold) else 0.35
            step_result = engine._critic_agent.enforce(step_result, critique, fail_threshold=fail_threshold)
            if not step_result.success:
                get_metrics().agent_failures[selected_agent.name] += 1
                engine._emit_agent_message(
                    agent_name=selected_agent.name,
                    step_id=execution_step.id,
                    message_type=MessageType.ERROR,
                    priority=MessagePriority.HIGH,
                    content=f"Step failed: {step_result.error or 'unknown'}",
                    confidence=0.2,
                    current_step_index=state.step,
                )

            engine._tool_learning.record(
                tool_name=execution_step.tool or step_result.tool_name,
                success=bool(step_result.success),
                latency=execution_latency,
                cost=float(step_result.cost or 0.0),
                confidence=float(critique.get("confidence", 0.0) or 0.0),
            )
            tracker = get_tracker(state.request_id)
            if tracker:
                step_preview = engine._build_step_partial_preview(step_result)
                tracker.update(
                    ProgressPhase.EXECUTING,
                    detail=f"STEP_EXECUTED:{execution_step.id}",
                    step=state.step,
                    total_steps=len(plan.steps),
                    partial_result=step_preview,
                )
            engine._agent_reputation.record(
                agent_name=selected_agent.name,
                success=bool(step_result.success),
                confidence=float(critique.get("confidence", 0.0) or 0.0),
                latency_ms=execution_latency,
            )

            engine._memory.store_step_result(execution_step.id, execution_step, step_result)
            state = engine._controller.record_step_result(state, step_result)
            state, _reflection, decision = await engine._reflection_manager.reflect_and_apply(
                engine=engine,
                state=state,
                step=execution_step,
                step_result=step_result,
                apply_research_guard=True,
            )
            if decision.should_terminate:
                break

        return state
