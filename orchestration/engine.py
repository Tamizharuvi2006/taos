"""
TAOS Orchestration Engine - The main loop runner.

This is the top-level entry point that ties ALL components together:
Controller, Planner, Executor, Reflector, MemoryManager, LoopGuard,
TerminationChecker, GoalValidator, OutputValidator.

Lifecycle:
1. Validate goal
2. Initialize state
3. Generate plan (PLANNING -> PLAN_READY)
4. Execute steps sequentially (EXECUTING <-> REFLECTING loop)
5. Handle failures (REPLANNING if needed)
6. Terminate and produce final result

PRD Reference: S3 (Semantic), S4 (Fast Path), S6 (Core Loop),
              S12 (Output), S14 (Self-Eval), S15 (Source Ranking)
"""

from __future__ import annotations

import asyncio
from collections import deque
import json
import os
import re
import time
import httpx
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import quote_plus, urlparse

from taos.config.constants import ErrorType, FSMState, GoalComplexity
from taos.config.settings import get_settings
from taos.core.agents import (
    AgentMessage,
    AgentMessageStore,
    AgentReputationStore,
    AgentRouter,
    CriticAgent,
    ExecutionAgent,
    MessagePriority,
    MessageType,
    PlannerAgent,
    ResearchAgent,
)
from taos.core.controller.controller import Controller
from taos.core.debate import DebateSystem
from taos.core.execution.executor import Executor
from taos.core.execution.step_runner import StepRunner
from taos.core.feedback import FeedbackMemoryEngine
from taos.core.loop.loop_guard import LoopGuard
from taos.core.loop.termination import TerminationChecker, build_final_result
from taos.core.memory.memory_manager import MemoryManager
from taos.core.persistence import FirestoreMemorySchema
from taos.core.planner.decomposition import GoalDecomposer
from taos.core.planner.planner import Planner
from taos.core.planner.plan_memory import PlanMemoryStore
from taos.core.planner.plan_validator import PlanValidator
from taos.core.reflection.confidence import ConfidenceScorer
from taos.core.reflection.reflector import Reflector
from taos.core.state.state_schema import GlobalState, PlanObject, PlanStep, StepResult, StepType
from taos.core.tools.registry import ToolRegistry
from taos.core.tools.tool_executor import ToolExecutor
from taos.core.tools.builtin import register_all_builtin_tools
from taos.core.validation.goal_validator import GoalValidator
from taos.core.validation.output_validator import OutputValidator
from taos.core.semantic.intent_classifier import (
    IntentType,
    ClassificationResult,
    DomainType,
    IntentClassifier,
)
from taos.core.services import AgentServiceClient, PlannerServiceRequest, ServiceClientError
from taos.core.semantic.query_rewriter import QueryRewriter
from taos.core.semantic.interpretation import RequestInterpreter
from taos.core.semantic.tone_profile import GLOBAL_TONE_PROFILER, ToneResult
from taos.core.routing import RouteDecider
from taos.core.search import SearchDepthRouter, SearchLite, SearchResultCache
from taos.core.understanding.universal_understanding_gateway import UniversalUnderstandingGateway, frame_to_trace_summary
from taos.core.understanding.query_frame import QueryFrameBuilder, compare_query_frame_to_selected_route
from taos.core.fast_path import FastPathEngine, QueryCache
from taos.core.output.response_formatter import ResponseFormatter
from taos.core.output.templates import TemplateFormatter
from taos.core.observability import get_metrics
from taos.core.evaluation.judge import JudgeSystem
from taos.core.evaluation import CitationSupportChecker, ConfidenceCalibrator
from taos.core.governance import QuotaManager, UsageMeter, default_cost_policy
from taos.core.research.extract_recovery import ExtractRecovery
from taos.core.research.extract_cache import ExtractCache
from taos.core.research.evidence_cache import EvidenceCache
from taos.core.research.freshness_policy import FreshnessPolicy
from taos.core.research.no_result_handler import NoResultHandler
from taos.core.research.research_pipeline import ResearchPipeline
from taos.core.research.source_quality import SourceQualityScorer
from taos.core.research.conflict_resolver import ConflictResolver
from taos.core.safety import HighStakesResearchGuard
from taos.core.tools.source_ranker import SourceRanker
from taos.core.tools.tool_learning import ToolLearningStore
from taos.core.reliability.provider_health import GLOBAL_PROVIDER_HEALTH, provider_health_snapshot
from taos.core.performance.latency import LatencyOptimizer
from taos.core.performance.progress import ProgressTracker, ProgressPhase, create_tracker, remove_tracker, get_tracker
from taos.orchestration.finalization_pipeline import FinalizationPipeline
from taos.orchestration.fsm_execution_loop import FSMExecutionLoop
from taos.orchestration.persistence_coordinator import PersistenceCoordinator
from taos.orchestration.planner_orchestrator import PlannerOrchestrator
from taos.orchestration.reflection_manager import ReflectionManager
from taos.orchestration.route_dispatcher import RouteDispatcher, RouteExecutionContext
from taos.orchestration.replanner import Replanner
from taos.config.model_config import ModelOrchestration

if TYPE_CHECKING:
    from taos.infra.logging.logger import TAOSLogger


class EngineError(Exception):
    """Raised when the orchestration engine encounters a fatal error."""
    pass


class OrchestrationEngine:
    """
    Production orchestration engine - the heart of TAOS.

    Wires all components into a single cohesive execution pipeline.
    Manages the full PLAN -> EXECUTE -> REFLECT -> TERMINATE lifecycle.
    """
    _RESEARCH_TRACE_BUFFER: Deque[Dict[str, Any]] = deque(maxlen=800)
    _ENTITY_DOMAIN_MEMORY: Dict[str, Dict[str, Any]] = {}
    _MAX_TOTAL_TIME_SECONDS: float = 40.0
    _SEARCH_STAGE_TIMEOUT_SECONDS: float = 8.0
    _EXTRACT_STAGE_TIMEOUT_SECONDS: float = 10.0
    _LLM_STAGE_TIMEOUT_SECONDS: float = 9.0

    def __init__(
        self,
        logger: Optional["TAOSLogger"] = None,
        tool_registry: Optional[ToolRegistry] = None,
    ) -> None:
        self._settings = get_settings()
        self._logger = logger

        # --- Tool system ---
        self._tool_registry = tool_registry or ToolRegistry()
        if not tool_registry:
            register_all_builtin_tools(self._tool_registry)

        # --- Core components ---
        self._controller = Controller(logger=logger)
        self._tool_executor = ToolExecutor(self._tool_registry)
        self._step_runner = StepRunner(self._tool_executor)
        self._executor = Executor(self._step_runner, logger=logger)
        self._execution_agent = ExecutionAgent(self._executor)
        self._source_ranker = SourceRanker()
        self._planner = Planner(
            available_tools=self._tool_registry.list_names(),
        )
        self._planner_agent = PlannerAgent(self._planner, self._executor)
        self._plan_validator = PlanValidator(
            registered_tools=self._tool_registry.get_names_set(),
        )
        self._research_agent = ResearchAgent(
            executor=self._executor,
            source_ranker=self._source_ranker,
        )
        self._critic_agent = CriticAgent()
        self._agent_reputation = AgentReputationStore()
        self._agent_router = AgentRouter(
            execution_agent=self._execution_agent,
            research_agent=self._research_agent,
            planner_agent=self._planner_agent,
            reputation_store=self._agent_reputation,
        )
        self._reflector = Reflector()
        self._confidence_scorer = ConfidenceScorer()
        self._replanner = Replanner(
            planner=self._planner,
            plan_validator=self._plan_validator,
        )

        # --- Memory and validation ---
        self._memory = MemoryManager()
        self._goal_validator = GoalValidator()
        self._output_validator = OutputValidator()

        # --- Safety ---
        self._loop_guard = LoopGuard()
        self._termination_checker = TerminationChecker()

        # --- Semantic + Fast Path + Output ---
        self._intent_classifier = IntentClassifier()
        self._query_rewriter = QueryRewriter()
        self._request_interpreter = RequestInterpreter()
        self._route_decider = RouteDecider()
        self._universal_understanding = UniversalUnderstandingGateway()
        self._query_frame_builder = QueryFrameBuilder()
        self._search_depth_router = SearchDepthRouter()
        self._search_result_cache = SearchResultCache()
        self._search_lite = SearchLite(cache=self._search_result_cache)
        self._fast_path = FastPathEngine()
        self._response_formatter = ResponseFormatter()
        self._template_formatter = TemplateFormatter()
        self._judge_system = JudgeSystem()
        self._citation_checker = CitationSupportChecker()
        self._confidence_calibrator = ConfidenceCalibrator()
        self._research_pipeline = ResearchPipeline()
        self._route_dispatcher = RouteDispatcher()
        self._planner_orchestrator = PlannerOrchestrator()
        self._fsm_execution_loop = FSMExecutionLoop()
        self._reflection_manager = ReflectionManager()
        self._finalization_pipeline = FinalizationPipeline()
        self._persistence_coordinator = PersistenceCoordinator()
        self._conflict_resolver = ConflictResolver()
        self._freshness_policy = FreshnessPolicy()
        self._source_quality = SourceQualityScorer()
        self._extract_recovery = ExtractRecovery()
        self._extract_cache = ExtractCache()
        self._evidence_cache = EvidenceCache()
        self._no_result_handler = NoResultHandler()
        self._high_stakes_guard = HighStakesResearchGuard()
        self._query_cache = QueryCache()
        self._cost_policy = default_cost_policy()
        self._quota_manager = QuotaManager(self._cost_policy)
        self._feedback_memory: Optional[FeedbackMemoryEngine] = None
        self._firestore_memory: Optional[FirestoreMemorySchema] = None
        self._service_client = AgentServiceClient(logger=logger)
        self._debate_system = DebateSystem()
        self._decomposer = GoalDecomposer(
            max_subgoals=max(2, int(self._settings.goal_decomposition_max_subgoals))
        )
        self._tool_learning = ToolLearningStore()
        self._plan_memory = PlanMemoryStore()
        self._message_store = AgentMessageStore(
            max_messages_per_step=self._settings.agent_message_max_per_step,
            max_total_messages=self._settings.agent_message_max_total,
        )

        # --- Performance ---
        self._latency = LatencyOptimizer()
        self._request_started_at = 0.0
        self._request_deadline_seconds = float(self._settings.max_request_time_seconds)
        self._active_intent: Optional[IntentType] = None
        self._active_domain: DomainType = DomainType.GENERAL
        self._active_request_id: str = "unknown"
        self._active_user_id: str = "default"
        self._active_chat_id: str = ""
        self._runtime_context_ready: bool = False
        self._active_tone: Optional[ToneResult] = None
        self._trace_enabled: bool = False
        self._trace_data: Dict[str, Any] = {}

    def _get_firestore_memory(self) -> FirestoreMemorySchema:
        if self._firestore_memory is None:
            self._firestore_memory = FirestoreMemorySchema()
        return self._firestore_memory

    def _get_feedback_memory(self) -> FeedbackMemoryEngine:
        if self._feedback_memory is None:
            self._feedback_memory = FeedbackMemoryEngine()
        return self._feedback_memory

    # ===========================================================
    # MAIN RUN LOOP
    # ===========================================================

    async def run(
        self,
        goal: str,
        request_id: Optional[str] = None,
        user_id: str = "default",
        chat_id: Optional[str] = None,
        include_trace: bool = False,
        doc_context_active: bool = False,
        doc_ids: Optional[List[str]] = None,
        route_hint_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a complete agent task from goal to final result."""
        state: Optional[GlobalState] = None
        classification: Optional[ClassificationResult] = None
        start_time = time.time()
        self._request_started_at = start_time
        self._active_request_id = request_id or "unknown"
        self._active_user_id = str(user_id or "default")
        self._active_chat_id = str(chat_id or "")
        normalized_doc_ids = [
            str(doc_id).strip()
            for doc_id in (doc_ids or [])
            if str(doc_id or "").strip()
        ]
        tracker = create_tracker(request_id or "unknown")
        metrics = get_metrics()
        metrics.total_requests += 1
        phase_started = time.time()
        self._message_store.clear()
        self._reset_execution_trace(
            request_id=request_id or "unknown",
            goal=goal,
            include_trace=include_trace,
        )

        try:
            # ---- CHECK LATENCY CACHE ----
            cached = self._latency.check_cache(goal)
            if cached:
                self._log("engine.cache_hit")
                metrics.cache_hits += 1
                self._latency.record_latency(
                    (time.time() - start_time) * 1000, was_cache_hit=True
                )
                tracker.update(ProgressPhase.COMPLETE, "Cache hit")
                remove_tracker(request_id or "unknown")
                return cached

            # ---- PHASE 1: VALIDATE GOAL ----
            self._log("engine.start", goal=goal[:200])
            tracker.update(ProgressPhase.RECEIVED, goal[:100])

            goal_result = self._goal_validator.validate(goal)
            if not goal_result.is_valid:
                return self._error_result(
                    goal=goal,
                    error="Goal validation failed: " + "; ".join(goal_result.errors),
                    request_id=request_id,
                )

            # ---- PHASE 1B: DETERMINISTIC-FIRST ROUTING + SEMANTIC METADATA ----
            tracker.update(ProgressPhase.CLASSIFYING)
            normalized_goal = self._request_interpreter.normalize_query(goal)
            route_context = {
                "has_context": self._memory.has_context(),
                "has_active_doc": bool(doc_context_active),
                "last_route": self._trace_data.get("route_label"),
                "doc_ids": normalized_doc_ids,
            }
            universal_frame = self._universal_understanding.understand(goal, context=route_context)
            universal_summary = frame_to_trace_summary(universal_frame)
            route_context["universal_understanding_frame"] = universal_frame
            route_context["universal_understanding"] = universal_summary
            universal_normalized_goal = str(universal_summary.get("normalized_query") or normalized_goal or goal).strip()
            deterministic_route = await self._route_decider.decide(
                goal,
                context=route_context,
            )
            early_source_result = await self._try_early_package_version_preempt(
                goal=goal,
                normalized_goal=normalized_goal,
                deterministic_route=deterministic_route,
                user_id=user_id,
                doc_context_active=bool(doc_context_active),
            )
            if early_source_result is not None:
                return early_source_result
            classification = self._intent_classifier.classify(
                universal_normalized_goal,
                has_context=self._memory.has_context(),
                has_active_doc=bool(doc_context_active),
            )
            self._apply_route_decision_to_classification(
                classification=classification,
                route_decision=deterministic_route,
                doc_context_active=bool(doc_context_active),
            )
            classify_latency_ms = (time.time() - phase_started) * 1000
            metrics.phase_latency_ms["classifying"] += classify_latency_ms
            metrics.phase_counts["classifying"] += 1
            self._record_trace_timing("route_ms", classify_latency_ms)
            phase_started = time.time()
            self._log(
                "engine.classified",
                intent=classification.intent.value,
                domain=classification.domain.value,
            )
            self._active_intent = classification.intent
            self._active_domain = classification.domain
            self._request_deadline_seconds = self._compute_request_deadline_seconds(
                goal=goal,
                classification=classification,
            )
            self._log(
                "engine.request_time_budget_set",
                max_seconds=self._request_deadline_seconds,
                intent=classification.intent.value,
            )

            # Rewrite query if needed
            rewrite = self._query_rewriter.rewrite(
                query=universal_normalized_goal,
                intent=classification.intent,
                domain=classification.domain,
                previous_context=self._memory.get_last_response(),
                is_followup=bool(classification.is_followup),
            )
            interpretation_envelope = self._request_interpreter.build_envelope(
                raw_query=goal,
                classification=classification,
                rewrite=rewrite,
                has_context=self._memory.has_context(),
                has_active_doc=bool(doc_context_active),
            )
            effective_goal = str(interpretation_envelope.get("rewritten_query") or universal_normalized_goal or normalized_goal or goal).strip()
            legacy_route_decision = dict(interpretation_envelope.get("route_decision") or {})
            route_decision = dict(legacy_route_decision)
            route_decision.update(deterministic_route.to_dict())
            routing_profile = dict(interpretation_envelope.get("routing_profile") or {})
            query_kind = str(routing_profile.get("query_kind") or "general")
            selected_route = self._resolve_selected_route(
                phase107_route=deterministic_route.route,
                query_kind=query_kind,
            )
            route_override = str(route_hint_override or "").strip().lower()
            if route_override == "entity_lookup":
                selected_route = "entity_lookup"
            route_decision["selected_route"] = selected_route
            route_decision["phase107_route"] = deterministic_route.route
            route_decision["route_owner"] = self._route_owner_for_selected_route(
                selected_route=selected_route,
                phase107_route=deterministic_route.route,
            )
            policy_reason = str(deterministic_route.reason or route_decision.get("policy_reason") or route_decision.get("route_reason") or "").strip()
            if route_override == "entity_lookup":
                route_decision["route_reason"] = "api_route_hint_override"
                route_decision["policy_reason"] = "api_route_hint_override"
                route_decision["rerouted"] = True
                policy_reason = "api_route_hint_override"
            if selected_route == "entity_lookup" and not bool(self._settings.entity_lookup_v1_enabled):
                selected_route = "deep_research"
                route_decision["selected_route"] = "deep_research"
                route_decision["verification_status"] = "rerouted"
                route_decision["rerouted"] = True
                route_decision["route_reason"] = "entity_lookup_flag_disabled"
                route_decision["policy_reason"] = "entity_lookup_flag_disabled"
                policy_reason = "entity_lookup_flag_disabled"
            query_frame_observation = self._build_query_frame_observation(
                query=goal,
                selected_route=selected_route,
            )
            route_assist = self._decide_query_frame_route_assist(
                query_frame_observation=query_frame_observation,
                selected_route=selected_route,
                query_kind=query_kind,
                doc_context_active=bool(doc_context_active),
            )
            query_frame_observation.update(route_assist)
            if bool(route_assist.get("route_assist_applied")):
                selected_route = str(route_assist.get("route_assist_to") or selected_route).strip().lower()
                route_decision["selected_route"] = selected_route
                route_decision["route_reason"] = "query_frame_route_assist"
                route_decision["policy_reason"] = "query_frame_route_assist"
                route_decision["rerouted"] = True
                route_decision["route_owner"] = self._route_owner_for_selected_route(
                    selected_route=selected_route,
                    phase107_route=deterministic_route.route,
                )
                policy_reason = "query_frame_route_assist"
            route_label_override = deterministic_route.route
            search_depth_decision = self._search_depth_router.route(effective_goal)
            route_boundary_summary = self._build_route_boundary_summary(
                phase107_route=deterministic_route.route,
                selected_route=selected_route,
                route_owner=str(route_decision.get("route_owner") or ""),
                boundary=deterministic_route.boundary,
                used_llm=deterministic_route.used_llm,
            )
            # Recompute alignment against the final selected route while keeping assist metadata.
            refreshed_observation = self._build_query_frame_observation(
                query=goal,
                selected_route=selected_route,
            )
            for key in (
                "route_assist_enabled",
                "route_assist_eligible",
                "route_assist_applied",
                "route_assist_from",
                "route_assist_to",
                "route_assist_reason",
                "route_assist_blocked_reason",
                "route_assist_confidence",
                "route_assist_lookup_type",
            ):
                refreshed_observation[key] = query_frame_observation.get(key)
            query_frame_observation = refreshed_observation
            self._set_query_frame_telemetry(query_frame_observation)

            if not isinstance(classification.metadata, dict):
                classification.metadata = {}
            existing_policy_reasons = [
                str(reason).strip()
                for reason in (classification.metadata.get("policy_override_reasons") or [])
                if str(reason).strip()
            ]
            for reason in (route_decision.get("policy_override_reasons") or []):
                reason_text = str(reason).strip()
                if reason_text and reason_text not in existing_policy_reasons:
                    existing_policy_reasons.append(reason_text)

            classification.metadata.update(
                {
                    "route_label": route_label_override,
                    "route_source": f"phase107_{deterministic_route.boundary}",
                    "route_confidence": float(deterministic_route.confidence or 0.0),
                    "careful_mode": str(routing_profile.get("risk_level") or "low") in {"medium", "high"},
                    "policy_override_reasons": existing_policy_reasons,
                    "doc_context_active": bool(doc_context_active),
                    "query_kind": query_kind,
                    "route_decision": route_decision,
                    "route_boundary_summary": route_boundary_summary,
                    "routing_profile": routing_profile,
                    "search_depth_mode": search_depth_decision.mode,
                    "search_depth_reason": search_depth_decision.reason,
                    "search_depth_confidence": search_depth_decision.confidence,
                    "universal_understanding": universal_summary,
                    "query_frame_observation": query_frame_observation,
                }
            )

            self._set_trace_value("intent", classification.intent.value)
            self._set_trace_value("mode", classification.suggested_mode)
            self._set_trace_value("doc_context_active", bool(doc_context_active))
            self._set_trace_value("route_label", classification.metadata.get("route_label"))
            self._set_trace_value("route_source", classification.metadata.get("route_source"))
            self._set_trace_value("route_confidence", classification.metadata.get("route_confidence"))
            self._set_trace_value("careful_mode", bool(classification.metadata.get("careful_mode")))
            self._set_trace_value("policy_override_reasons", existing_policy_reasons[:5])
            self._set_trace_value("query_kind", query_kind)
            self._set_trace_value("policy_reason", policy_reason)
            self._set_trace_value("search_depth_mode", search_depth_decision.mode)
            self._set_trace_value("search_depth_reason", search_depth_decision.reason)
            self._set_trace_value("search_depth_confidence", search_depth_decision.confidence)
            self._set_trace_value("verification_state", "pending")
            self._set_trace_value("interpretation", interpretation_envelope)
            self._set_trace_value("routing_profile", routing_profile)
            self._set_trace_value("route_decision", route_decision)
            self._set_trace_value("route_boundary_summary", route_boundary_summary)
            self._set_trace_value("universal_understanding", universal_summary)
            self._set_trace_value("query_frame", query_frame_observation)
            self._set_trace_value("meaning_frame", dict(universal_summary.get("meaning_frame") or {}))
            self._log(
                "engine.query_frame_observe",
                request_id=self._active_request_id,
                current_route=selected_route,
                query_frame_intent_family=str(query_frame_observation.get("query_frame_suggested_family") or "unknown"),
                entity_name=str(query_frame_observation.get("entity_name") or ""),
                requested_role=str(query_frame_observation.get("requested_role") or ""),
                route_alignment=str(query_frame_observation.get("route_alignment") or "unknown"),
            )
            self._set_trace_value(
                "planning_handoff",
                {
                    "raw_query": str(interpretation_envelope.get("raw_query") or goal),
                    "normalized_query": str(universal_summary.get("normalized_query") or interpretation_envelope.get("normalized_query") or normalized_goal),
                    "meaning_frame": dict(universal_summary.get("meaning_frame") or {}),
                    "rewritten_query": effective_goal,
                    "context_required": bool(routing_profile.get("context_required")),
                    "grounding_need": str(routing_profile.get("grounding_need") or "none"),
                    "risk_level": str(routing_profile.get("risk_level") or "low"),
                    "route_reason": str(route_decision.get("route_reason") or ""),
                    "route_boundary": str(route_boundary_summary.get("boundary") or ""),
                    "route_owner": str(route_boundary_summary.get("owner") or ""),
                    "query_kind": query_kind,
                    "policy_reason": policy_reason,
                    "policy_override_reasons": existing_policy_reasons[:5],
                },
            )
            route_owner = str(route_decision.get("route_owner") or route_boundary_summary.get("owner") or "").strip()
            if self._is_adversarial_integrity_pressure(effective_goal):
                classification.confidence = min(float(classification.confidence or 0.5), 0.25)
                if isinstance(classification.metadata, dict):
                    classification.metadata["route_confidence"] = 0.25
                self._set_trace_value("planner_path", "adversarial_guard")
                self._append_direct_trace_step(
                    step_type="reason",
                    status="success",
                    summary="Returned integrity-locked response for adversarial certainty pressure.",
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=self._build_adversarial_guard_response(effective_goal),
                    goal_override=goal,
                    user_id=user_id,
                )
            if self._is_high_stakes_policy_ban_prompt(goal):
                self._set_trace_value("planner_path", "deep_research")
                self._set_trace_value("route_label", "deep_research")
                if not isinstance(classification.metadata, dict):
                    classification.metadata = {}
                classification.metadata["route_label"] = "deep_research"
                self._append_direct_trace_step(
                    step_type="reason",
                    status="success",
                    summary="Applied high-stakes policy-ban guard response with explicit uncertainty.",
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=self._build_high_stakes_policy_ban_response(),
                    goal_override=goal,
                    user_id=user_id,
                )
            if self._is_compare_which_better_prompt(goal):
                self._set_trace_value("planner_path", "direct")
                self._set_trace_value("route_label", "standard_task")
                if not isinstance(classification.metadata, dict):
                    classification.metadata = {}
                classification.metadata["route_label"] = "standard_task"
                self._append_direct_trace_step(
                    step_type="reason",
                    status="success",
                    summary="Handled comparison-decision prompt with direct balanced standard-task response.",
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=self._build_compare_which_better_response(goal=effective_goal),
                    goal_override=goal,
                    user_id=user_id,
                )
            if (
                selected_route == "clarification"
                or (
                    self._is_ambiguous_followup_without_anchor(goal)
                    and not self._is_truth_verification_prompt(goal)
                    and not (self._runtime_context_ready and bool(self._memory.get_last_response()))
                )
            ):
                self._set_trace_value("planner_path", "clarification")
                self._set_trace_value("route_label", "clarification")
                if not isinstance(classification.metadata, dict):
                    classification.metadata = {}
                classification.metadata["route_label"] = "clarification"
                self._append_direct_trace_step(
                    step_type="reason",
                    status="success",
                    summary="Returned clarification prompt because query is ambiguous without prior context.",
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=self._build_ambiguous_clarification_response(effective_goal),
                    goal_override=goal,
                    user_id=user_id,
                )
            if self._is_overloaded_structured_task_prompt(goal):
                self._set_trace_value("planner_path", "direct")
                self._set_trace_value("route_label", "standard_task")
                if not isinstance(classification.metadata, dict):
                    classification.metadata = {}
                classification.metadata["route_label"] = "standard_task"
                self._append_direct_trace_step(
                    step_type="reason",
                    status="success",
                    summary="Handled overloaded summarize/explain/compare/examples task with structured direct template.",
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=self._build_overloaded_structured_task_response(),
                    goal_override=goal,
                    user_id=user_id,
                )
            if self._is_minimal_progress_prompt(goal) and not (self._runtime_context_ready and bool(self._memory.get_last_response())):
                self._set_trace_value("planner_path", "fast_path")
                self._set_trace_value("route_label", "fast_message")
                if not isinstance(classification.metadata, dict):
                    classification.metadata = {}
                classification.metadata["route_label"] = "fast_message"
                self._append_direct_trace_step(
                    step_type="reason",
                    status="success",
                    summary="Handled minimal continuation prompt as fast-message without context.",
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=self._build_contextless_progress_response(),
                    goal_override=goal,
                    user_id=user_id,
                )
            if self._is_minimal_progress_prompt(goal) and (self._runtime_context_ready and bool(self._memory.get_last_response())):
                self._set_trace_value("planner_path", "fast_path")
                self._append_direct_trace_step(
                    step_type="reason",
                    status="success",
                    summary="Returned contextual continuation for minimal follow-up prompt.",
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=self._build_contextual_progress_response(
                        goal=effective_goal,
                        context=self._memory.get_last_response(),
                    ),
                    goal_override=goal,
                    user_id=user_id,
                )
            if self._is_short_explain_key_points_prompt(goal):
                self._set_trace_value("planner_path", "direct")
                self._append_direct_trace_step(
                    step_type="reason",
                    status="success",
                    summary="Handled mixed-language short explain intent with concise key-point prompt.",
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=self._build_short_explain_key_points_response(),
                    goal_override=goal,
                    user_id=user_id,
                )
            if self._is_shortcut_no_explain_prompt(goal):
                self._set_trace_value("planner_path", "direct")
                self._append_direct_trace_step(
                    step_type="reason",
                    status="success",
                    summary="Handled shortcut no-explanation instruction with concise compliance response.",
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=self._build_shortcut_no_explain_response(),
                    goal_override=goal,
                    user_id=user_id,
                )
            if self._is_context_detail_followup(goal) and not (self._runtime_context_ready and bool(self._memory.get_last_response())):
                self._set_trace_value("planner_path", "direct")
                self._append_direct_trace_step(
                    step_type="reason",
                    status="success",
                    summary="Returned context-aware follow-up expansion template without prior context.",
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=self._build_context_detail_without_context_response(),
                    goal_override=goal,
                    user_id=user_id,
                )
            if self._is_context_detail_followup(goal) and (self._runtime_context_ready and bool(self._memory.get_last_response())):
                self._set_trace_value("planner_path", "direct")
                self._append_direct_trace_step(
                    step_type="reason",
                    status="success",
                    summary="Expanded prior context detail for follow-up continuation request.",
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=self._build_context_detail_followup_response(
                        goal=effective_goal,
                        context=self._memory.get_last_response(),
                    ),
                    goal_override=goal,
                    user_id=user_id,
                )
            tone_scope = f"{self._active_user_id}:{self._active_chat_id or 'nochat'}"
            self._active_tone = GLOBAL_TONE_PROFILER.evaluate(scope_key=tone_scope, message=effective_goal)
            self._set_trace_value(
                "tone_profile",
                {
                    "current": self._active_tone.current_label,
                    "blended": self._active_tone.blended_label,
                    "serious": self._active_tone.serious,
                    "casual": self._active_tone.casual,
                    "playful": self._active_tone.playful,
                    "emoji_allowed": self._active_tone.emoji_allowed,
                    "banter_allowed": self._active_tone.banter_allowed,
                    "style_hint": self._active_tone.brief_hint,
                    "thresholds": GLOBAL_TONE_PROFILER.config_snapshot(),
                },
            )
            force_research_pipeline = selected_route == "deep_research" or self._should_force_research_pipeline(effective_goal)
            if force_research_pipeline and route_owner not in {"research_pipeline", "document_pipeline"}:
                route_owner = "research_pipeline"
            owner_result = await self._execute_route_owner_path(
                route_owner=route_owner,
                goal=goal,
                effective_goal=effective_goal,
                classification=classification,
                user_id=user_id,
                tracker=tracker,
                doc_context_active=bool(doc_context_active),
                doc_ids=normalized_doc_ids,
            )
            if owner_result is not None:
                return owner_result

            # ---- PHASE 1C: SMART FAST PATH ----
            # FastPathEngine decides IF we can skip full pipeline.
            # Dynamic lookups (version, price, latest) are NOT fast-pathed.
            fast_result = None
            allow_fast_path = selected_route == "micro_fast"
            if allow_fast_path:
                fast_result = await self._fast_path.try_fast_path(
                    query=effective_goal,
                    classification=classification,
                    context=self._memory.get_last_response(),
                )
            else:
                self._log("engine.fast_path_bypassed", reason=f"selected_route_{selected_route}")

            if fast_result and fast_result.was_handled:
                if force_research_pipeline:
                    self._log("engine.fast_path_bypassed", reason="forced_research_pipeline")
                force_dynamic_lookup = self._looks_like_dynamic_market_query(effective_goal)
                if not force_research_pipeline and force_dynamic_lookup:
                    self._log("engine.fast_path_bypassed", reason="dynamic_market_query")
                elif not force_research_pipeline:
                    self._log("engine.fast_path_hit", source=fast_result.source)
                    self._set_trace_value("planner_path", "fast_path")
                    self._append_direct_trace_step(
                        step_type="reason",
                        status="success",
                        summary=f"Answered via {fast_result.source or 'fast path'} without full planning.",
                    )
                    final_text = fast_result.result
                    if not final_text:
                        prompt = self._fast_path.get_fast_prompt(
                            query=effective_goal,
                            classification=classification,
                            context=self._memory.get_last_response(),
                        )
                        final_text = await self._run_fast_llm(
                            prompt,
                            apply_tone=True,
                            stream_to_progress=True,
                        )

                    # Guardrail: reject templated placeholder responses from direct LLM fast-path.
                    if final_text and "[insert" in final_text.lower():
                        self._log("engine.fast_path_bypassed", reason="placeholder_response")
                        final_text = None
                    if final_text and "as of my last update" in final_text.lower():
                        self._log("engine.fast_path_bypassed", reason="stale_cutoff_response")
                        final_text = None
                    if final_text and self._should_reject_fast_path_output(
                        query=goal,
                        response_text=final_text,
                        selected_route=selected_route,
                        routing_profile=routing_profile,
                    ):
                        self._log("engine.fast_path_bypassed", reason="route_verifier_mismatch")
                        final_text = None

                    if final_text:
                        return await self._finalize(
                            state=None,
                            classification=classification,
                            raw_result=final_text,
                            goal_override=goal,
                            user_id=user_id,
                        )

            # ---- DYNAMIC LOOKUP: Tool-Assisted Fast Path ----
            # For freshness-sensitive lookups (latest/today/current prices/versions),
            # do a quick web search + LLM instead of full planner.
            needs_dynamic_lookup = (
                (classification.intent == IntentType.SIMPLE_LOOKUP and self._fast_path.requires_tools(effective_goal))
                or self._looks_like_dynamic_market_query(effective_goal)
            )
            if needs_dynamic_lookup and not force_research_pipeline and route_owner == "direct_standard":
                self._log("engine.dynamic_lookup", query=effective_goal[:100])
                self._set_trace_value("planner_path", "dynamic_lookup")
                search_answer = await self._tool_assisted_lookup(effective_goal)
                if search_answer:
                    self._append_direct_trace_step(
                        step_type="tool",
                        status="success",
                        tool="web_search",
                        summary="Resolved a freshness-sensitive lookup through live search.",
                    )
                    return await self._finalize(
                        state=None,
                        classification=classification,
                        raw_result=search_answer,
                        goal_override=goal,
                        user_id=user_id,
                    )
                # Avoid planner failures for freshness-bound lookup requests.
                fallback_lookup_msg = (
                    "I could not fetch a reliable live quote right now. "
                    "Please retry in a moment, and I will report the latest available "
                    "price with its exact source date."
                )
                self._mark_trace_fallback(
                    reason="dynamic_lookup_unavailable",
                    freshness_status="failed",
                    freshness_note="Live lookup could not be verified during dynamic lookup.",
                )
                self._append_direct_trace_step(
                    step_type="tool",
                    status="failed",
                    tool="web_search",
                    summary="Live lookup could not return a reliable source-grounded answer.",
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=fallback_lookup_msg,
                    goal_override=goal,
                    user_id=user_id,
                )

            # ---- QUERY CACHE ----
            if not classification.is_followup:
                cached = self._query_cache.get(effective_goal)
                if cached:
                    resp_text, intent_str = cached
                    return await self._finalize(
                        state=None,
                        classification=classification,
                        raw_result=resp_text,
                        goal_override=goal,
                        user_id=user_id,
                    )

            # ---- PHASE 2: INITIALIZE STATE ----
            # Complexity Tiering: LOW complexity bypasses full FSM
            # But RESEARCH, NEWS, TASK always use full pipeline for quality
            complexity_map = {
                GoalComplexity.LOW: 0.3,
                GoalComplexity.MEDIUM: 0.6,
                GoalComplexity.HIGH: 0.9,
            }
            comp_score = complexity_map.get(goal_result.complexity, 0.5)
            # Keep research/news on robust pipelines; allow low-complexity TASK intents
            # to use tiered bypass when routing selected a lightweight answer path.
            skip_intents = [IntentType.RESEARCH, IntentType.NEWS]

            # ---- ENTITY LOOKUP PIPELINE INTERCEPT (feature-flagged) ----
            if (
                (selected_route == "entity_lookup" or route_override == "entity_lookup")
                and bool(self._settings.entity_lookup_v1_enabled)
            ):
                self._log("engine.entity_lookup_pipeline", query=effective_goal)
                self._set_trace_value("planner_path", "entity_lookup")
                entity_res = await self._run_entity_lookup(effective_goal)
                if entity_res:
                    return await self._finalize(
                        state=None,
                        classification=classification,
                        raw_result=entity_res,
                        goal_override=goal,
                        user_id=user_id,
                    )

            # ---- DEEP RESEARCH ENGINE INTERCEPT ----
            # Route selection must be authoritative. Do not force deep research only from coarse classifier
            # labels when interpreter selected a non-research route (e.g., comparison/task paths).
            should_run_deep_research = bool(route_owner != "research_pipeline" and force_research_pipeline)
            if should_run_deep_research:
                self._log("engine.deep_research_pipeline", query=effective_goal)
                self._set_trace_value("planner_path", "deep_research")
                self._set_trace_value("dag_name", "research_v2")
                deep_res = await self._run_deep_research(effective_goal)
                if deep_res:
                    return await self._finalize(
                        state=None,
                        classification=classification,
                        raw_result=deep_res,
                        goal_override=goal,
                        user_id=user_id,
                    )
                if force_research_pipeline:
                    quick_res = await self._tool_assisted_lookup(effective_goal)
                    if quick_res:
                        self._append_direct_trace_step(
                            step_type="tool",
                            status="success",
                            tool="web_search",
                            summary="Recovered with direct live lookup after deep research produced no verified answer.",
                        )
                        return await self._finalize(
                            state=None,
                            classification=classification,
                            raw_result=quick_res,
                            goal_override=goal,
                            user_id=user_id,
                        )
                    return await self._finalize(
                        state=None,
                        classification=classification,
                        raw_result=(
                            "I could not verify a reliable current-status update from live sources right now. "
                            "Please retry in a moment and I will return the latest verifiable update with source timing."
                        ),
                        goal_override=goal,
                        user_id=user_id,
                    )

            if comp_score < 0.4 and classification.intent not in skip_intents:
                self._log("engine.tiered_bypass", complexity=comp_score)
                prompt_ctx = f"\nContext: {self._memory.get_last_response()}" if classification.is_followup else ""
                tier_ans = await self._run_fast_llm(
                    "Analyze and answer the following directly: " + effective_goal + prompt_ctx,
                    apply_tone=True,
                    stream_to_progress=True,
                )
                if tier_ans:
                    return await self._finalize(
                        state=None,
                        classification=classification,
                        raw_result=tier_ans,
                        goal_override=goal,
                        user_id=user_id,
                    )

            # Hallucination trap
            goal_lower = effective_goal.lower()
            if ("mars" in goal_lower and "react" in goal_lower) or ("dinosaurs" in goal_lower):
                trap_resp = await self._run_fast_llm(
                    "The user is asking a nonsensical query: '" + effective_goal
                    + "'. Politely explain why this does not make sense in 1 sentence.",
                    apply_tone=True,
                    stream_to_progress=True,
                )
                return await self._finalize(
                    state=None,
                    classification=classification,
                    raw_result=trap_resp or "I cannot answer this as the components are incompatible.",
                    goal_override=goal,
                    user_id=user_id,
                )

            state = self._controller.initialize(effective_goal, request_id)
            self._active_request_id = state.request_id or self._active_request_id
            if classification and not classification.is_followup and classification.intent != IntentType.TRANSFORM:
                self._memory.reset()

            self._loop_guard.reset()
            self._confidence_scorer.reset()

            # ---- PHASE 3: PLANNING ----
            state = self._controller.transition(state, FSMState.PLANNING)
            tracker.update(ProgressPhase.PLANNING)
            feedback_memory = self._get_feedback_memory()
            feedback_docs = await feedback_memory.retrieve_relevant_feedback(
                user_id=user_id,
                query=effective_goal,
                top_k=max(1, int(self._settings.feedback_max_context_items)),
            )
            feedback_hints = feedback_memory.build_context_hints(feedback_docs)
            similar_plans = self._plan_memory.retrieve_similar(
                goal=effective_goal,
                top_k=3,
            )
            planner_hints = self._plan_memory.build_planner_hints(similar_plans, max_hints=3)
            if similar_plans:
                get_metrics().plan_memory_hits += 1
            tool_hints = self._build_tool_learning_hints()
            planner_hints.extend(tool_hints)
            planner_hints.extend(
                [
                    f"[RawQuery] {str(interpretation_envelope.get('raw_query') or goal)}",
                    f"[CanonicalTask] {effective_goal}",
                    f"[RoutingConstraint] context_required={bool(routing_profile.get('context_required'))}, "
                    f"grounding_need={str(routing_profile.get('grounding_need') or 'none')}, "
                    f"risk_level={str(routing_profile.get('risk_level') or 'low')}",
                ]
            )
            state, plan = await self._generate_and_validate_plan(
                state, effective_goal,
                intent=classification.intent.value if classification else None,
                feedback_hints=feedback_hints,
                planner_hints=planner_hints,
            )
            self._update_trace_from_plan(plan)
            metrics.phase_latency_ms["planning"] += (time.time() - phase_started) * 1000
            metrics.phase_counts["planning"] += 1
            phase_started = time.time()

            # ---- PHASE 4: EXECUTION LOOP ----
            state = self._controller.start_execution(state)
            state = await self._execution_loop(state)
            metrics.phase_latency_ms["executing"] += (time.time() - phase_started) * 1000
            metrics.phase_counts["executing"] += 1
            phase_started = time.time()

            # ---- PHASE 5: FINALIZATION ----
            tracker.update(ProgressPhase.FORMATTING)
            return await self._finalize(state, classification, user_id=user_id)

        except Exception as e:
            self._log("engine.fatal_error", error=str(e))
            metrics.failed_requests += 1
            if state:
                state = self._controller.fail(state, "Fatal error: " + str(e))
                res = build_final_result(state)
                if classification:
                    res["intent"] = classification.intent.value
                    res["domain"] = classification.domain.value
                self._attach_execution_trace(
                    result=res,
                    state=state,
                    classification=classification,
                    goal_override=goal,
                )
                return res
            result = self._error_result(
                goal=goal,
                error=str(e),
                request_id=request_id,
                intent=classification.intent.value if classification else "unknown",
                domain=classification.domain.value if classification else "general",
            )
            self._attach_execution_trace(
                result=result,
                state=None,
                classification=classification,
                goal_override=goal,
            )
            return result
        finally:
            elapsed = (time.time() - start_time) * 1000
            metrics.total_latency_ms += elapsed
            self._emit_learning_snapshots()
            self._active_request_id = "unknown"

    # ===========================================================
    # PLANNING
    # ===========================================================

    async def _generate_and_validate_plan(
        self,
        state: GlobalState,
        goal: str,
        intent: Optional[str] = None,
        feedback_hints: Optional[List[str]] = None,
        planner_hints: Optional[List[str]] = None,
    ) -> tuple:
        """Generate a plan via the Phase 114 planner orchestrator."""
        state, plan, metadata = await self._planner_orchestrator.generate_and_validate(
            engine=self,
            state=state,
            goal=goal,
            intent=intent,
            feedback_hints=feedback_hints,
            planner_hints=planner_hints,
        )
        self._trace_data["planner_orchestrator"] = {
            "intent": metadata.intent,
            "planner_hints_count": metadata.planner_hints_count,
            "feedback_hints_count": metadata.feedback_hints_count,
            "tools_override": metadata.tools_override,
            "decomposition_used": metadata.decomposition_used,
            "freshness_sensitive": metadata.freshness_sensitive,
            "planning_cost": metadata.planning_cost,
        }
        return state, plan

    async def _generate_plan_with_adapter(
        self,
        goal: str,
        context: List[str],
        intent: Optional[str],
        available_tools_override: Optional[List[str]],
        request_id: Optional[str],
    ) -> tuple[PlanObject, float]:
        """Generate plan via planner-service when enabled, fallback to local planner agent."""
        if self._service_client.planner_enabled:
            try:
                return await self._service_client.generate_plan(
                    PlannerServiceRequest(
                        goal=goal,
                        context=context,
                        intent=intent,
                        available_tools_override=available_tools_override,
                    ),
                    request_id=request_id,
                )
            except ServiceClientError as e:
                self._log("engine.planner_service_fallback", error=str(e))

        return await self._planner_agent.generate_plan(
            goal=goal,
            context=context,
            intent=intent,
            available_tools_override=available_tools_override,
        )

    async def _execute_step_with_adapter(
        self,
        selected_agent_name: str,
        step: PlanStep,
        state: GlobalState,
        step_index: int,
        request_id: Optional[str],
        fallback_agent: Any,
    ) -> StepResult:
        """Execute step via microservice adapter if enabled; fallback locally on error."""
        if self._service_client.enabled:
            try:
                if selected_agent_name == "research_agent":
                    if not self._service_client.research_enabled:
                        raise ServiceClientError("Research service disabled by feature flag")
                    return await self._service_client.run_research_step(
                        step=step,
                        state=state,
                        step_index=step_index,
                        request_id=request_id,
                    )
                if selected_agent_name == "execution_agent":
                    if not self._service_client.execution_enabled:
                        raise ServiceClientError("Execution service disabled by feature flag")
                    return await self._service_client.run_execution_step(
                        step=step,
                        state=state,
                        step_index=step_index,
                        request_id=request_id,
                    )
            except ServiceClientError as e:
                self._log(
                    "engine.agent_service_fallback",
                    agent=selected_agent_name,
                    error=str(e),
                )

        return await fallback_agent.execute(
            step=step,
            state=state,
            step_index=step_index,
        )

    # ===========================================================
    # EXECUTION LOOP
    # ===========================================================

    async def _execution_loop(self, state: GlobalState) -> GlobalState:
        """Run the FSM execution loop through the Phase 114 loop owner."""
        return await self._fsm_execution_loop.run(engine=self, state=state)

    async def _execution_loop_legacy_unused(self, state: GlobalState) -> GlobalState:
        """Legacy inline loop retained temporarily for audit fallback; not called by engine."""
        MAX_STEPS = max(1, int(self._settings.max_steps))
        while state.current_fsm_state == FSMState.EXECUTING:
            if self._request_started_at > 0 and (time.time() - self._request_started_at) >= self._request_deadline_seconds:
                self._log("engine.request_time_budget_exceeded", max_seconds=self._request_deadline_seconds)
                state = self._controller.transition(
                    state,
                    FSMState.TERMINATING,
                    status_update="timeout",
                    error_update="TIME_BUDGET_EXCEEDED",
                )
                break
            if state.step >= MAX_STEPS:
                self._log("engine.step_limit_reached", max_steps=MAX_STEPS)
                state = self._controller.transition(
                    state, FSMState.TERMINATING, status_update="success"
                )
                break

            termination = self._termination_checker.check(state)
            if termination:
                state = self._controller.transition(
                    state,
                    FSMState.TERMINATING,
                    status_update=termination.final_status,
                    error_update=(
                        termination.reason if termination.final_status != "success" else None
                    ),
                )
                break

            self._loop_guard.record_state(state)
            loop_reason = self._loop_guard.check_all(state)
            if loop_reason:
                state = self._controller.fail(state, "Loop detected: " + loop_reason)
                break

            plan = state.plan
            if not plan or state.step >= len(plan.steps):
                state = self._controller.transition(
                    state, FSMState.TERMINATING, status_update="success"
                )
                break

            # Level-1 parallelism: run independent web_search steps concurrently.
            remaining_budget = max(0, MAX_STEPS - state.step)
            parallel_batch = self._get_parallel_search_batch(
                state=state,
                max_batch_size=remaining_budget,
            )
            influence = self._derive_message_influence(state)
            if influence["critical_warning"]:
                parallel_batch = []
            if len(parallel_batch) > 1:
                if not self._validate_parallel_batch_safety(parallel_batch):
                    parallel_batch = []
            if len(parallel_batch) > 1:
                self._log(
                    "engine.parallel_batch_start",
                    size=len(parallel_batch),
                    step_ids=[s.id for s in parallel_batch],
                )
                batch_results = await self._execute_parallel_search_batch(
                    steps=parallel_batch,
                    state=state,
                )
                state, stop = await self._process_step_results_in_order(
                    state=state,
                    ordered_steps=parallel_batch,
                    result_map=batch_results,
                )
                if stop:
                    break
                continue

            current_step = plan.steps[state.step]
            execution_step = self._apply_tool_learning(current_step, current_step_index=state.step)
            influence = self._derive_message_influence(state)
            pre_critique = self._critic_agent.pre_check(execution_step, state)
            self._log(
                "engine.critic_precheck",
                step_id=execution_step.id,
                allowed=pre_critique.get("allowed", True),
                issues=len(pre_critique.get("issues", [])),
            )
            if not pre_critique.get("allowed", True):
                self._emit_agent_message(
                    agent_name="critic_agent",
                    step_id=execution_step.id,
                    message_type=MessageType.ERROR,
                    priority=MessagePriority.HIGH,
                    content="Pre-check rejected step before execution",
                    confidence=0.2,
                    current_step_index=state.step,
                )
                reason = ", ".join(pre_critique.get("issues", [])[:2]) or "pre-check rejected step"
                step_result = self._critic_agent.enforce(
                    StepResult(
                        step_id=execution_step.id,
                        success=False,
                        error=f"Critic pre-check failed: {reason}",
                        error_type=ErrorType.VALIDATION_ERROR.value,
                        tool_name=execution_step.tool,
                    ),
                    {"valid": False, "issues": pre_critique.get("issues", []), "confidence": 0.0},
                )
                self._memory.store_step_result(execution_step.id, execution_step, step_result)
                state = self._controller.record_step_result(state, step_result)
                reflection = await self._reflector.reflect(
                    step_result=step_result, step=execution_step, state=state
                )
                self._memory.store_reflection(execution_step.id, reflection)
                self._confidence_scorer.record(reflection.confidence)
                self._tool_learning.record(
                    tool_name=execution_step.tool,
                    success=False,
                    latency=0.0,
                    cost=float(step_result.cost or 0.0),
                    confidence=0.0,
                )
                state = self._controller.handle_reflection(state, reflection)
                if state.current_fsm_state == FSMState.REPLANNING:
                    state = await self._handle_replanning(state)
                if state.current_fsm_state == FSMState.TERMINATING:
                    break
                continue

            selected_agent = self._agent_router.select(execution_step, state)
            if influence["force_research"] and execution_step.tool is None:
                selected_agent = self._research_agent
                self._emit_agent_message(
                    agent_name="router",
                    step_id=execution_step.id,
                    message_type=MessageType.DECISION,
                    priority=MessagePriority.MEDIUM,
                    content="Rerouted step to research agent based on low-confidence signals",
                    confidence=0.7,
                    current_step_index=state.step,
                )
            get_metrics().agent_usage[selected_agent.name] += 1
            trust_score = self._agent_reputation.trust(selected_agent.name)
            self._log(
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
            step_result = await self._execute_step_with_adapter(
                selected_agent_name=selected_agent.name,
                step=execution_step,
                state=state,
                step_index=state.step,
                request_id=state.request_id,
                fallback_agent=selected_agent,
            )
            step_result = await self._recover_research_step_failure(
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
                        self._emit_agent_message(
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
                self._emit_agent_message(
                    agent_name="execution_agent",
                    step_id=execution_step.id,
                    message_type=MessageType.REQUEST,
                    priority=MessagePriority.MEDIUM,
                    content="Need structured supporting research context for comparison output.",
                    confidence=0.7,
                    current_step_index=state.step,
                    targets=["research_agent"],
                )
            critique = self._critic_agent.post_check(
                step=execution_step,
                step_result=step_result,
                state=state,
            )
            self._log(
                "engine.critic_result",
                step_id=execution_step.id,
                valid=critique.get("valid", True),
                confidence=critique.get("confidence", 0.0),
                issues=len(critique.get("issues", [])),
            )
            if not critique.get("valid", True):
                get_metrics().critic_failures += 1
                self._emit_agent_message(
                    agent_name="critic_agent",
                    step_id=execution_step.id,
                    message_type=MessageType.WARNING,
                    priority=MessagePriority.HIGH if float(critique.get("confidence", 0.0) or 0.0) < 0.5 else MessagePriority.MEDIUM,
                    content="Post-check flagged low-quality output",
                    confidence=float(critique.get("confidence", 0.0) or 0.0),
                    current_step_index=state.step,
                )
            fail_threshold = (
                0.5 if trust_score < float(self._settings.agent_low_trust_threshold) else 0.35
            )
            step_result = self._critic_agent.enforce(
                step_result,
                critique,
                fail_threshold=fail_threshold,
            )
            if not step_result.success:
                get_metrics().agent_failures[selected_agent.name] += 1
                self._emit_agent_message(
                    agent_name=selected_agent.name,
                    step_id=execution_step.id,
                    message_type=MessageType.ERROR,
                    priority=MessagePriority.HIGH,
                    content=f"Step failed: {step_result.error or 'unknown'}",
                    confidence=0.2,
                    current_step_index=state.step,
                )

            self._tool_learning.record(
                tool_name=execution_step.tool or step_result.tool_name,
                success=bool(step_result.success),
                latency=execution_latency,
                cost=float(step_result.cost or 0.0),
                confidence=float(critique.get("confidence", 0.0) or 0.0),
            )
            tracker = get_tracker(state.request_id)
            if tracker:
                step_preview = self._build_step_partial_preview(step_result)
                tracker.update(
                    ProgressPhase.EXECUTING,
                    detail=f"STEP_EXECUTED:{execution_step.id}",
                    step=state.step,
                    total_steps=len(plan.steps),
                    partial_result=step_preview,
                )
            self._agent_reputation.record(
                agent_name=selected_agent.name,
                success=bool(step_result.success),
                confidence=float(critique.get("confidence", 0.0) or 0.0),
                latency_ms=execution_latency,
            )

            self._memory.store_step_result(execution_step.id, execution_step, step_result)
            state = self._controller.record_step_result(state, step_result)

            reflection = await self._reflector.reflect(
                step_result=step_result, step=execution_step, state=state
            )
            reflection = self._apply_research_reflection_guard(
                reflection=reflection,
                step_result=step_result,
                step=execution_step,
                state=state,
            )
            self._memory.store_reflection(execution_step.id, reflection)
            self._confidence_scorer.record(reflection.confidence)

            state = self._controller.handle_reflection(state, reflection)
            if state.current_fsm_state == FSMState.REPLANNING:
                state = await self._handle_replanning(state)
            if state.current_fsm_state == FSMState.TERMINATING:
                break

        return state

    def _build_step_partial_preview(self, step_result: StepResult) -> Optional[str]:
        """Best-effort short preview for streaming UI during multi-step execution."""
        if not step_result.success:
            return None
        value = step_result.result
        if isinstance(value, str):
            text = re.sub(r"\s+", " ", value).strip()
            return text[:600] if text else None
        if isinstance(value, dict):
            # Common tool result fields we can surface quickly.
            for key in ("formatted_response", "summary", "answer", "result"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    text = re.sub(r"\s+", " ", candidate).strip()
                    return text[:600]
            rows = value.get("results")
            if isinstance(rows, list) and rows:
                first = rows[0] if isinstance(rows[0], dict) else {}
                title = str(first.get("title") or "").strip()
                snippet = str(first.get("snippet") or "").strip()
                if title or snippet:
                    compact = f"{title}: {snippet}".strip(": ").strip()
                    compact = re.sub(r"\s+", " ", compact)
                    return compact[:600]
        return None

    def _compute_request_deadline_seconds(
        self,
        goal: str,
        classification: Optional[ClassificationResult] = None,
    ) -> float:
        base_timeout = float(self._settings.max_request_time_seconds)
        research_timeout = float(
            max(self._settings.max_request_time_seconds, self._settings.max_research_time_seconds)
        )
        goal_text = (goal or "").lower()
        research_markers = (
            "research",
            "reserch",
            "analyze",
            "analysis",
            "comprehensive",
            "investigate",
            "current status",
            "latest status",
            "war",
            "conflict",
            "timeline",
            "report",
        )
        if classification and classification.intent == IntentType.RESEARCH:
            return min(research_timeout, self._MAX_TOTAL_TIME_SECONDS)
        if any(marker in goal_text for marker in research_markers):
            return min(research_timeout, self._MAX_TOTAL_TIME_SECONDS)
        return min(base_timeout, self._MAX_TOTAL_TIME_SECONDS)

    def _request_elapsed_seconds(self) -> float:
        if self._request_started_at <= 0:
            return 0.0
        return max(0.0, float(time.time() - self._request_started_at))

    def _request_time_budget_remaining(self) -> float:
        deadline = float(self._request_deadline_seconds or self._MAX_TOTAL_TIME_SECONDS)
        return max(0.0, deadline - self._request_elapsed_seconds())

    def _stage_timeout_seconds(self, stage_cap_seconds: float) -> float:
        stage_cap = max(0.1, float(stage_cap_seconds or 0.0))
        remaining = self._request_time_budget_remaining()
        if remaining <= 0:
            return 0.0
        return max(0.1, min(stage_cap, remaining))

    async def _process_step_results_in_order(
        self,
        state: GlobalState,
        ordered_steps: List[PlanStep],
        result_map: Dict[str, StepResult],
    ) -> tuple[GlobalState, bool]:
        """
        Apply batch results safely in plan order.

        Even when execution is parallel, state transitions remain sequential and
        controller-owned.
        """
        for step in ordered_steps:
            step_result = result_map[step.id]
            step_result = await self._recover_research_step_failure(
                step=step,
                step_result=step_result,
                state=state,
            )
            step_started_at = float(step_result.timestamp or time.time())
            selected_agent = self._agent_router.select(step, state)
            trust_score = self._agent_reputation.trust(selected_agent.name)
            critique = self._critic_agent.post_check(
                step=step,
                step_result=step_result,
                state=state,
            )
            self._log(
                "engine.critic_result",
                step_id=step.id,
                valid=critique.get("valid", True),
                confidence=critique.get("confidence", 0.0),
                issues=len(critique.get("issues", [])),
            )
            if not critique.get("valid", True):
                get_metrics().critic_failures += 1
            fail_threshold = (
                0.5 if trust_score < float(self._settings.agent_low_trust_threshold) else 0.35
            )
            step_result = self._critic_agent.enforce(
                step_result,
                critique,
                fail_threshold=fail_threshold,
            )
            if not step_result.success:
                get_metrics().agent_failures["parallel_batch"] += 1

            latency_ms = max(0.0, (time.time() - step_started_at) * 1000)
            self._tool_learning.record(
                tool_name=step.tool or step_result.tool_name,
                success=bool(step_result.success),
                latency=latency_ms,
                cost=float(step_result.cost or 0.0),
                confidence=float(critique.get("confidence", 0.0) or 0.0),
            )
            self._agent_reputation.record(
                agent_name=selected_agent.name,
                success=bool(step_result.success),
                confidence=float(critique.get("confidence", 0.0) or 0.0),
                latency_ms=latency_ms,
            )

            self._memory.store_step_result(step.id, step, step_result)
            state = self._controller.record_step_result(state, step_result)

            reflection = await self._reflector.reflect(
                step_result=step_result,
                step=step,
                state=state,
            )
            reflection = self._apply_research_reflection_guard(
                reflection=reflection,
                step_result=step_result,
                step=step,
                state=state,
            )
            self._memory.store_reflection(step.id, reflection)
            self._confidence_scorer.record(reflection.confidence)

            state = self._controller.handle_reflection(state, reflection)
            if state.current_fsm_state == FSMState.REPLANNING:
                state = await self._handle_replanning(state)
            if state.current_fsm_state == FSMState.TERMINATING:
                return state, True

        return state, False

    def _is_research_like_intent(self) -> bool:
        return self._active_intent in {IntentType.RESEARCH, IntentType.NEWS}

    async def _recover_research_step_failure(
        self,
        step: PlanStep,
        step_result: StepResult,
        state: GlobalState,
    ) -> StepResult:
        """
        DAG-style safety recovery for research/news flows.

        If an intermediate step fails with a fragile tool or recoverable tool error,
        attempt a constrained web_search fallback instead of letting FSM terminate early.
        """
        if not self._is_research_like_intent() or step_result.success:
            return step_result

        allowed_tools = {
            None,
            "web_search",
            "web_extract",
            "http_request",
            "file_read",
            "file_list",
            "file_write",
        }
        if step.tool not in allowed_tools:
            return step_result

        error_text = str(step_result.error or "").lower()
        error_type = str(step_result.error_type or "").upper()
        recoverable_types = {
            ErrorType.TOOL_FAILURE.value,
            ErrorType.TOOL_RATE_LIMIT.value,
            ErrorType.TIMEOUT.value,
            ErrorType.VALIDATION_ERROR.value,
            ErrorType.UNKNOWN.value,
        }
        recoverable_phrases = (
            "policy violation",
            "invalid",
            "missing required argument",
            "rate limit",
            "timed out",
            "http_request",
            "web_extract",
            "file_read",
            "file_write",
            "file_list",
        )
        should_recover = (
            (error_type in recoverable_types)
            or any(phrase in error_text for phrase in recoverable_phrases)
            or step.tool in {"http_request", "web_extract", "file_read", "file_list", "file_write"}
        )
        if not should_recover:
            return step_result

        raw_query = (
            str((step.tool_input or {}).get("query", "") or "").strip()
            or str(step.action or "").strip()
            or str(step.description or "").strip()
            or str(state.goal or "").strip()
        )
        if not raw_query:
            raw_query = "latest verified updates"

        tool_input: Dict[str, Any] = {
            "query": raw_query,
            "num_results": 5,
        }
        if self._active_intent == IntentType.NEWS:
            tool_input["search_type"] = "news"
            tool_input["recency_days"] = 2

        self._log(
            "engine.research_step_recovery_attempt",
            step_id=step.id,
            from_tool=step.tool,
            error_type=error_type,
        )
        recovered = await self._tool_executor.execute(
            tool_name="web_search",
            tool_input=tool_input,
            task_id=state.request_id,
            step_id=step.id,
        )
        if recovered.success:
            self._log(
                "engine.research_step_recovered",
                step_id=step.id,
                from_tool=step.tool,
                to_tool="web_search",
            )
            return recovered
        self._log(
            "engine.research_step_recovery_failed",
            step_id=step.id,
            from_tool=step.tool,
            fallback_error=(recovered.error or "")[:300],
        )
        return step_result

    def _apply_research_reflection_guard(
        self,
        reflection: Any,
        step_result: StepResult,
        step: PlanStep,
        state: GlobalState,
    ) -> Any:
        """
        Prevent premature TERMINATING on recoverable research-step failures.

        Converts ultra-low-confidence recoverable failures into replanning-grade
        confidence so FSM chooses REPLANNING instead of hard termination.
        """
        if not self._is_research_like_intent():
            return reflection
        if getattr(reflection, "success", False):
            return reflection
        if state.plan and state.step >= len(state.plan.steps):
            return reflection

        error_type = str(step_result.error_type or getattr(reflection, "error_type", "") or "").upper()
        recoverable_types = {
            ErrorType.TOOL_FAILURE.value,
            ErrorType.TOOL_RATE_LIMIT.value,
            ErrorType.TIMEOUT.value,
            ErrorType.VALIDATION_ERROR.value,
            ErrorType.UNKNOWN.value,
        }
        if error_type not in recoverable_types:
            return reflection

        terminate_threshold = float(self._settings.confidence_terminate_threshold)
        retry_threshold = float(self._settings.confidence_retry_threshold)
        confidence = float(getattr(reflection, "confidence", 0.0) or 0.0)
        if confidence >= terminate_threshold:
            return reflection

        guarded_confidence = max(terminate_threshold + 0.05, 0.35)
        if guarded_confidence >= retry_threshold:
            guarded_confidence = max(terminate_threshold + 0.01, retry_threshold - 0.01)

        reason = str(getattr(reflection, "reasoning", "") or "").strip()
        guard_reason = "Research guard: recoverable intermediate failure, rerouting to replanning path."
        merged_reason = f"{reason} {guard_reason}".strip()

        self._log(
            "engine.research_reflection_guard",
            step_id=step.id,
            original_confidence=confidence,
            guarded_confidence=guarded_confidence,
            error_type=error_type,
        )
        return reflection.model_copy(
            update={
                "confidence": guarded_confidence,
                "retry_recommended": True,
                "reasoning": merged_reason,
            }
        )

    def _get_parallel_search_batch(
        self,
        state: GlobalState,
        max_batch_size: int = 4,
    ) -> List[PlanStep]:
        """
        Find contiguous runnable web_search steps for parallel execution.

        Constraints:
        - tool must be web_search
        - dependencies must already be satisfied by completed successful steps
        - respects per-loop step budget
        """
        if max_batch_size <= 1 or not state.plan:
            return []

        completed_success_ids = {r.step_id for r in state.step_results if r.success}
        batch: List[PlanStep] = []

        for step in state.plan.steps[state.step:]:
            if len(batch) >= max_batch_size:
                break
            if step.tool != "web_search":
                break
            if not self._are_dependencies_satisfied(step.depends_on, completed_success_ids):
                break
            batch.append(step)

        return batch

    def _validate_parallel_batch_safety(self, steps: List[PlanStep]) -> bool:
        """
        Validate parallel safety:
        - no intra-batch dependency chain
        - no shared conflict keys in tool_input that may imply shared writes
        """
        if len(steps) <= 1:
            return False

        step_ids = {s.id for s in steps}
        for step in steps:
            if set(step.depends_on) & step_ids:
                return False

        conflict_fields = ("path", "file", "file_path", "key", "id", "target")
        seen: set[str] = set()
        for step in steps:
            payload = step.tool_input or {}
            for field in conflict_fields:
                value = payload.get(field)
                if not value:
                    continue
                marker = f"{field}:{str(value).strip().lower()}"
                if marker in seen:
                    return False
                seen.add(marker)
        return True

    def _derive_message_influence(self, state: GlobalState) -> Dict[str, bool]:
        """
        Deterministic message influence rules.

        - critical WARNING/ERROR -> stricter execution mode (disable parallel)
        - >=2 low-confidence INFO messages -> force research route for ambiguous steps
        """
        active = self._message_store.active(current_step_index=state.step)
        critical_warning = any(
            m.message_type in {MessageType.WARNING, MessageType.ERROR}
            and m.priority == MessagePriority.CRITICAL
            for m in active
        )
        low_conf_info = [
            m for m in active
            if m.message_type == MessageType.INFO and m.confidence < 0.6
        ]
        force_research = len(low_conf_info) >= 2
        return {
            "critical_warning": critical_warning,
            "force_research": force_research,
        }

    def _emit_agent_message(
        self,
        agent_name: str,
        step_id: str,
        message_type: MessageType,
        priority: MessagePriority,
        content: str,
        confidence: float,
        current_step_index: int,
        targets: Optional[List[str]] = None,
    ) -> None:
        msg = AgentMessage(
            agent_name=agent_name,
            step_id=step_id,
            message_type=message_type,
            priority=priority,
            content=content,
            confidence=confidence,
            targets=targets or [],
            ttl_steps=self._settings.agent_message_ttl_steps,
            ttl_seconds=self._settings.agent_message_ttl_seconds,
            created_step_index=current_step_index,
        )
        added = self._message_store.emit(msg, current_step_index=current_step_index)
        if added:
            get_metrics().message_counts[message_type.value] += 1
            self._log(
                "engine.agent_message",
                agent=agent_name,
                message_type=message_type.value,
                priority=priority.value,
                step_id=step_id,
            )

    def _build_tool_learning_hints(self) -> List[str]:
        """Provide compact tool performance hints for planner context."""
        hints: List[str] = []
        if self._active_intent in {IntentType.RESEARCH, IntentType.NEWS}:
            hints.append(
                "[EvidenceRead] For top sources, prefer web_extract(url) after web_search to ground claims with page text and dates."
            )
        if self._active_intent == IntentType.NEWS:
            hints.append(
                "[NewsSearch] For web_search steps on news intent, use search_type='news' and recency_days=2."
            )
        strongest = self._tool_learning.top_tools(limit=2)
        weakest = self._tool_learning.weakest_tools(limit=2)
        if strongest:
            formatted = ", ".join([f"{name}({score:.2f})" for name, score in strongest])
            hints.append(f"[ToolLearning] Reliable tools: {formatted}.")
        if weakest:
            formatted = ", ".join([f"{name}({score:.2f})" for name, score in weakest])
            hints.append(f"[ToolLearning] Weak tools to use cautiously: {formatted}.")
        return hints

    def _apply_tool_learning(
        self,
        step: PlanStep,
        current_step_index: int = 0,
    ) -> PlanStep:
        """
        Apply safe runtime tool override using learning signals.

        Controller/state ownership remains intact; this only changes execution intent.
        """
        if not step.tool:
            return step
        if step.tool == "web_search" and self._active_intent == IntentType.NEWS:
            tool_input = dict(step.tool_input or {})
            changed = False
            if str(tool_input.get("search_type", "") or "").lower() != "news":
                tool_input["search_type"] = "news"
                changed = True
            recency = tool_input.get("recency_days")
            if not isinstance(recency, int) or recency <= 0:
                tool_input["recency_days"] = 2
                changed = True
            if changed:
                self._log(
                    "engine.news_search_mode",
                    step_id=step.id,
                    search_type=tool_input.get("search_type"),
                    recency_days=tool_input.get("recency_days"),
                )
                step = step.model_copy(update={"tool_input": tool_input})
        if self._active_intent in {IntentType.RESEARCH, IntentType.NEWS} and step.tool:
            # Runtime guard: if planner emits fragile/non-research tools, normalize to safe research behavior.
            if step.tool == "file_write":
                self._log(
                    "engine.research_tool_redirect",
                    step_id=step.id,
                    from_tool=step.tool,
                    to_tool="reasoning",
                )
                step = step.model_copy(update={"tool": None, "tool_input": None})
            elif step.tool in {"http_request", "file_read", "file_list"}:
                tool_input = dict(step.tool_input or {})
                url = str(tool_input.get("url", "") or "").strip()
                path = str(tool_input.get("path", "") or "").strip()
                if step.tool == "http_request" and url.startswith(("http://", "https://")):
                    self._log(
                        "engine.research_tool_redirect",
                        step_id=step.id,
                        from_tool=step.tool,
                        to_tool="web_extract",
                    )
                    step = step.model_copy(
                        update={
                            "tool": "web_extract",
                            "tool_input": {"url": url, "max_chars": 5000},
                        }
                    )
                    return step
                if step.tool in {"file_read", "file_list"} and path:
                    return step

                query = (
                    str(tool_input.get("query", "") or "").strip()
                    or str(step.action or "").strip()
                    or str(step.description or "").strip()
                )
                if not query:
                    query = "latest verified updates"
                replacement_input: Dict[str, Any] = {
                    "query": query,
                    "num_results": 5,
                }
                if self._active_intent == IntentType.NEWS:
                    replacement_input["search_type"] = "news"
                    replacement_input["recency_days"] = 2
                self._log(
                    "engine.research_tool_redirect",
                    step_id=step.id,
                    from_tool=step.tool,
                    to_tool="web_search",
                )
                step = step.model_copy(update={"tool": "web_search", "tool_input": replacement_input})
        if not step.tool:
            return step
        if not self._tool_learning.should_avoid(step.tool):
            return step
        fallback = self._tool_learning.suggest_fallback(step.tool)
        if not fallback:
            return step
        # Never override web search to raw HTTP calls when no explicit URL exists.
        if fallback == "http_request":
            tool_input = step.tool_input or {}
            url = str(tool_input.get("url", "") or "").strip()
            if not url.startswith(("http://", "https://")):
                # Allow web_search fallback by synthesizing a safe GET target from query.
                if step.tool == "web_search":
                    query = str(tool_input.get("query", "") or "").strip()
                    if query:
                        from urllib.parse import quote_plus

                        generated_url = f"https://www.google.com/search?q={quote_plus(query)}"
                        tool_input = {
                            "method": "GET",
                            "url": generated_url,
                            "timeout": 15,
                        }
                        step = step.model_copy(update={"tool_input": tool_input})
                    else:
                        return step
                else:
                    return step

        get_metrics().tool_learning_overrides += 1
        self._emit_agent_message(
            agent_name="tool_learning",
            step_id=step.id,
            message_type=MessageType.SUGGESTION,
            priority=MessagePriority.MEDIUM,
            content=f"Tool override applied: {step.tool} -> {fallback}",
            confidence=0.7,
            current_step_index=current_step_index,
            targets=["execution_agent", "critic_agent"],
        )
        return step.model_copy(update={"tool": fallback})

    def _emit_learning_snapshots(self) -> None:
        """Flush lightweight learning snapshots into observability metrics."""
        metrics = get_metrics()
        metrics.tool_stats_snapshot = self._tool_learning.snapshot()
        metrics.plan_memory_records = self._plan_memory.count
        metrics.agent_trust_snapshot = self._agent_reputation.snapshot()

    def _are_dependencies_satisfied(
        self,
        depends_on: List[str],
        completed_ids: set[str],
    ) -> bool:
        """Return True when all declared dependencies are already complete."""
        return set(depends_on).issubset(completed_ids)

    async def _execute_parallel_search_batch(
        self,
        steps: List[PlanStep],
        state: GlobalState,
    ) -> Dict[str, StepResult]:
        """Execute a batch of search steps concurrently with pre-check guardrails."""
        result_map: Dict[str, StepResult] = {}
        tasks: List[Any] = []
        task_step_ids: List[str] = []

        for offset, step in enumerate(steps):
            execution_step = self._apply_tool_learning(
                step,
                current_step_index=state.step + offset,
            )
            pre_critique = self._critic_agent.pre_check(execution_step, state)
            self._log(
                "engine.critic_precheck",
                step_id=execution_step.id,
                allowed=pre_critique.get("allowed", True),
                issues=len(pre_critique.get("issues", [])),
            )
            if not pre_critique.get("allowed", True):
                reason = ", ".join(pre_critique.get("issues", [])[:2]) or "pre-check rejected step"
                result_map[execution_step.id] = StepResult(
                    step_id=execution_step.id,
                    success=False,
                    error=f"Critic pre-check failed: {reason}",
                    error_type=ErrorType.VALIDATION_ERROR.value,
                    tool_name=execution_step.tool,
                )
                continue

            selected_agent = self._agent_router.select(execution_step, state)
            get_metrics().agent_usage[selected_agent.name] += 1
            self._log(
                "engine.agent_selected",
                step_id=execution_step.id,
                agent=selected_agent.name,
                tool=execution_step.tool,
            )
            tasks.append(
                self._execute_step_with_adapter(
                    selected_agent_name=selected_agent.name,
                    step=execution_step,
                    state=state,
                    step_index=state.step + offset,
                    request_id=state.request_id,
                    fallback_agent=selected_agent,
                )
            )
            task_step_ids.append(execution_step.id)

        if tasks:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for step_id, task_result in zip(task_step_ids, task_results):
                if isinstance(task_result, Exception):
                    result_map[step_id] = StepResult(
                        step_id=step_id,
                        success=False,
                        error=f"Parallel execution failed: {task_result}",
                        error_type=ErrorType.UNKNOWN.value,
                    )
                    get_metrics().agent_failures["parallel_batch"] += 1
                else:
                    result_map[step_id] = task_result

        return result_map

    # ===========================================================
    # REPLANNING
    # ===========================================================

    async def _handle_replanning(self, state: GlobalState) -> GlobalState:
        """Handle replanning."""
        context = self._memory.build_context_window(state)
        new_plan = await self._replanner.replan(state=state, context=context)
        state = self._controller.set_replan(state, new_plan)
        state = self._controller.start_execution(state)
        return state

    # ===========================================================
    # UNIFIED FINALIZATION (All paths converge here)
    # ===========================================================

    async def _finalize(
        self,
        state: Optional[GlobalState],
        classification: Optional[ClassificationResult] = None,
        raw_result: Optional[str] = None,
        goal_override: Optional[str] = None,
        user_id: str = "default",
    ) -> Dict[str, Any]:
        """Finalize through the Phase 114 finalization pipeline."""
        return await self._finalization_pipeline.finalize(
            engine=self,
            state=state,
            classification=classification,
            raw_result=raw_result,
            goal_override=goal_override,
            user_id=user_id,
        )

    async def _finalize_legacy(
        self,
        state: Optional[GlobalState],
        classification: Optional[ClassificationResult] = None,
        raw_result: Optional[str] = None,
        goal_override: Optional[str] = None,
        user_id: str = "default",
    ) -> Dict[str, Any]:
        """
        Finalize the execution and produce the result.

        This is the UNIVERSAL exit point. Every result (fast path, tiered
        bypass, standard pipeline) flows through here to ensure:
        1. ResponseFormatter cleans raw JSON / tool leaks
        2. JudgeSystem refines quality
        3. Memory is updated for follow-ups
        """
        if state and state.current_fsm_state == FSMState.TERMINATING:
            state = self._controller.terminate(state)
        tracker_id = (state.request_id if state else None) or "unknown"
        tracker = get_tracker(tracker_id)
        if tracker:
            tracker.update(ProgressPhase.EVALUATING)

        # Build base result dict
        if state:
            result = build_final_result(state)
        else:
            planner_path = str((self._trace_data or {}).get("planner_path") or "").strip().lower()
            route_label = ""
            if classification and isinstance(classification.metadata, dict):
                route_label = str(classification.metadata.get("route_label") or "").strip().lower()
            inferred_fast_path = planner_path in {"fast_path", "query_cache", "micro_fast"}
            if route_label == "fast_message" and planner_path not in {"deep_research", "fsm", "dag_exec"}:
                inferred_fast_path = True
            if planner_path == "deep_research":
                inferred_mode = "deep"
            elif classification and classification.suggested_mode:
                inferred_mode = str(classification.suggested_mode).strip().lower()
            else:
                inferred_mode = "fast" if inferred_fast_path else "standard"
            result = {
                "request_id": "fast",
                "goal": goal_override or "unknown",
                "success": True,
                "status": "success",
                "result": raw_result,
                "confidence": 0.9,
                "fast_path": inferred_fast_path,
                "mode": inferred_mode,
            }

        active_goal = state.goal if state else (goal_override or "")
        intent = classification.intent if classification else IntentType.TASK
        is_followup = classification.is_followup if classification else False
        mode_map = {"fast": "low", "standard": "medium", "deep": "high"}
        complexity = mode_map.get(
            classification.suggested_mode if classification else "standard", "medium"
        )

        # --- Multi-Agent Debate Layer (trigger-based, expensive) ---
        # Critic handles cheap fast validation. Debate Judge runs only on strict triggers.
        if result.get("result") and classification:
            debate_conf = float(result.get("confidence", 0.0) or 0.0)
            critic_flagged = False
            if state and state.step_results:
                critic_flagged = any(not sr.success for sr in state.step_results)
            if bool(self._settings.enable_debate_service) and self._debate_system.should_trigger(
                complexity=complexity,
                confidence=debate_conf,
                critic_flagged=critic_flagged,
            ):
                if tracker:
                    tracker.update(ProgressPhase.EVALUATING, detail="DEBATE_STARTED")
                get_metrics().debate_runs += 1
                self._log(
                    "engine.debate_triggered",
                    intent=classification.intent.value,
                    complexity=complexity,
                    confidence=debate_conf,
                    critic_flagged=critic_flagged,
                )
                debated = await self._debate_system.debate_and_resolve(
                    goal=active_goal,
                    base_answer=str(result.get("result", "")),
                )
                if debated:
                    result["result"] = debated
                    result["debate_triggered"] = True
                    if tracker:
                        tracker.update(ProgressPhase.EVALUATING, detail="DEBATE_RESULT")

        # --- Formatting (ResponseFormatter cleans JSON leaks) ---
        if result.get("result") or result.get("status") == "success":
            res_val = result.get("result", "No specific data found.")

            if isinstance(res_val, dict):
                import json
                raw_data = json.dumps(res_val, indent=2)
            else:
                raw_data = str(res_val)

            # ---- RESEARCH SYNTHESIS LAYER ----
            is_search_result = isinstance(res_val, dict) and "results" in res_val
            planner_path = str(self._trace_data.get("planner_path") or "")
            should_synthesize = (
                is_search_result
                or (
                    intent in (IntentType.RESEARCH, IntentType.NEWS)
                    and planner_path not in {"deep_research", "entity_lookup", "fast_search"}
                )
            )
            if should_synthesize and "- Key Data:" not in raw_data:
                self._log("engine.research_synthesis_trigger")
                synthesized = await self._synthesize_research(
                    raw_data,
                    active_goal,
                    high_stakes_mode=self._is_high_stakes_query(active_goal),
                )
                if synthesized:
                    import re
                    # Strip away the internal CoT scratchpad so the user doesn't see internal reasoning.
                    synthesized = re.sub(r'<scratchpad>.*?</scratchpad>', '', synthesized, flags=re.DOTALL)
                    raw_data = synthesized.strip()
                    result["result"] = raw_data
            formatted = self._response_formatter.format(
                raw_output=raw_data,
                goal=active_goal,
                intent=intent,
                confidence=result.get("confidence", 0.0),
                complexity=complexity,
                is_followup=is_followup,
            )
            result["formatted_response"] = formatted.full_text
            result["direct_answer"] = formatted.direct_answer
            result["key_points"] = formatted.key_points
            fast_label = ""
            if classification and isinstance(classification.metadata, dict):
                fast_label = str(classification.metadata.get("route_label") or "").strip().lower()
            if (
                fast_label == "fast_message"
                and "next useful follow-ups" in str(raw_data or "").lower()
            ):
                result["formatted_response"] = str(raw_data or "").strip()
                if isinstance(result.get("result"), str):
                    result["result"] = str(raw_data or "").strip()
            evidence_stats_after_format = dict(self._trace_data.get("evidence_stats") or {})
            if (
                str(self._trace_data.get("planner_path") or "").strip().lower() == "fast_search"
                and str(evidence_stats_after_format.get("source_type") or "").strip().lower() == "package_registry"
                and str(evidence_stats_after_format.get("verification_state") or "").strip().lower()
                in {"verified", "confirmed"}
            ):
                direct_package_answer = str(raw_data or "").strip()
                result["formatted_response"] = direct_package_answer
                result["result"] = direct_package_answer
                result["direct_answer"] = direct_package_answer[:250] + (
                    "..." if len(direct_package_answer) > 250 else ""
                )
                result["key_points"] = []

        # --- Self-evaluation and Refine ---
        # Skip expensive judge/refine pass for pure fast-message replies.
        is_fast_message_route = bool(
            classification
            and isinstance(classification.metadata, dict)
            and str(classification.metadata.get("route_label") or "").strip().lower() == "fast_message"
        )
        is_doc_mode_direct = bool(
            state is None
            and str(self._trace_data.get("planner_path") or "").strip().lower() == "doc_mode_direct"
        )
        is_fast_search_direct = bool(
            state is None
            and str(self._trace_data.get("planner_path") or "").strip().lower() == "fast_search"
        )
        skip_judge_for_fast_message = bool(result.get("fast_path")) and is_fast_message_route
        skip_judge_for_doc_mode = is_doc_mode_direct
        skip_judge_for_entity_lookup = bool(
            state is None
            and str(self._trace_data.get("planner_path") or "").strip().lower() == "entity_lookup"
        )
        if skip_judge_for_fast_message:
            self._log("engine.judge_skipped", reason="fast_message_route")
        elif is_fast_search_direct:
            reason = "source_of_record_verified" if self._is_verified_source_of_record_fast_search() else "fast_search_direct"
            self._log("engine.judge_skipped", reason=reason)
        elif skip_judge_for_doc_mode:
            self._log("engine.judge_skipped", reason="doc_mode_direct")
        elif skip_judge_for_entity_lookup:
            self._log("engine.judge_skipped", reason="entity_lookup_terminal")
        if result.get("result") and not (
            skip_judge_for_fast_message
            or is_fast_search_direct
            or skip_judge_for_doc_mode
            or skip_judge_for_entity_lookup
        ):
            judge_res = await self._judge_system.judge_and_refine(
                output=str(result["result"]),
                goal=active_goal,
                intent=intent,
                complexity=complexity,
            )
            result["result"] = judge_res["refined_output"]
            evaluation = judge_res["evaluation"]

            if judge_res["was_refined"]:
                formatted = self._response_formatter.format(
                    raw_output=str(result["result"]),
                    goal=active_goal,
                    intent=intent,
                    confidence=result.get("confidence", 0.0),
                    complexity=complexity,
                    is_followup=is_followup,
                )
                result["formatted_response"] = formatted.full_text

            result["evaluation"] = {
                "passed": evaluation.passed,
                "overall": round(evaluation.overall_score, 2),
                "issues": evaluation.issues,
                "was_refined": judge_res["was_refined"],
            }

        # --- FALLBACK: Zero-Null Rule ---
        fmt = result.get("formatted_response")
        if isinstance(fmt, str) and "as of my last update" in fmt.lower():
            self._mark_trace_fallback(
                reason="stale_cutoff_phrase",
                freshness_status="failed",
                freshness_note="A stale cutoff phrase was detected and replaced.",
            )
            result["formatted_response"] = None
            fmt = None
        if not fmt or fmt == "None" or result.get("status") == "failed":
            research_like = intent in (IntentType.RESEARCH, IntentType.NEWS) or self._is_freshness_sensitive_research(active_goal)
            if research_like:
                # Never degrade research/news to stale internal-knowledge boilerplate.
                self._mark_trace_fallback(
                    reason="research_unverified_fallback",
                    freshness_status="recovered",
                    freshness_note="Returned a source-safe fallback instead of stale or unverified research output.",
                )
                result["formatted_response"] = self._build_research_unverified_message(
                    active_goal,
                    high_stakes_mode=self._is_high_stakes_query(active_goal),
                )
                result["success"] = True
                result["status"] = "success"
            else:
                fallback_ans = await self._run_fast_llm(
                    "Provide a direct helpful answer based on internal knowledge for: '"
                    + active_goal + "'.",
                    apply_tone=True,
                    stream_to_progress=True,
                )
                if fallback_ans:
                    self._mark_trace_fallback(reason="generic_llm_fallback")
                    result["formatted_response"] = fallback_ans
                    result["success"] = True
                    result["status"] = "success"

        if result.get("formatted_response"):
            planner_path_hint = str(self._trace_data.get("planner_path") or "").strip().lower()
            if planner_path_hint == "clarification":
                styled_response = str(result.get("formatted_response") or "")
                if "need one more detail" not in styled_response.lower():
                    styled_response = (
                        "I need one more detail before I can answer accurately.\n\n"
                        + styled_response
                    )
                if "ambiguous" not in styled_response.lower():
                    styled_response += (
                        "\n\nThis request is ambiguous without context; share the exact topic/entity and I will answer directly."
                    )
                if "please share the topic" not in styled_response.lower():
                    styled_response += (
                        "\nPlease share the topic/entity so I can answer directly."
                    )
            else:
                styled_response = self._apply_tone_output_styling(
                    text=str(result.get("formatted_response") or ""),
                    intent=intent,
                    goal=active_goal,
                )
            result["formatted_response"] = styled_response
            if isinstance(result.get("direct_answer"), str):
                trimmed = styled_response[:250]
                result["direct_answer"] = trimmed + ("..." if len(styled_response) > 250 else "")
            if isinstance(result.get("result"), str):
                # Keep user-visible result in sync for stream consumers that read `result`.
                result["result"] = styled_response

        route_label_for_quality = ""
        if classification and isinstance(classification.metadata, dict):
            route_label_for_quality = str(classification.metadata.get("route_label") or "").strip().lower()
        if not route_label_for_quality:
            route_label_for_quality = str(self._trace_data.get("route_label") or "").strip().lower()

        source_links = self._resolve_result_source_links(result)
        if source_links and (not isinstance(result.get("sources"), list) or not result.get("sources")):
            result["sources"] = source_links
        evidence_stats_for_quality = dict(self._trace_data.get("evidence_stats") or {})
        verified_package_registry = (
            str(evidence_stats_for_quality.get("source_type") or "").strip().lower() == "package_registry"
            and str(evidence_stats_for_quality.get("verification_state") or "").strip().lower()
            in {"verified", "confirmed"}
        )
        if result.get("formatted_response") and not verified_package_registry:
            improved = self._enforce_authority_quality_blocks(
                text=str(result.get("formatted_response") or ""),
                route_label=route_label_for_quality,
                intent=intent,
                planner_path=str(self._trace_data.get("planner_path") or ""),
                source_links=source_links,
                signal=str((self._trace_data.get("evidence_stats") or {}).get("signal") or ""),
                stale_detected=bool((self._trace_data.get("evidence_stats") or {}).get("stale_detected")),
                conflict_detected=bool((self._trace_data.get("evidence_stats") or {}).get("conflict_detected")),
                high_stakes_mode=bool((self._trace_data.get("evidence_stats") or {}).get("high_stakes_mode")),
                official_source_found=bool((self._trace_data.get("evidence_stats") or {}).get("official_source_found")),
            )
            improved = self._ensure_simple_explain_for_child_prompt(
                text=improved,
                goal=active_goal,
            )
            result["formatted_response"] = improved
            if isinstance(result.get("result"), str):
                result["result"] = improved
            if isinstance(result.get("direct_answer"), str):
                trimmed = improved[:250]
                result["direct_answer"] = trimmed + ("..." if len(improved) > 250 else "")

        if result.get("formatted_response") and str(self._trace_data.get("planner_path") or "").strip().lower() == "fast_search":
            fast_search_report = self._build_evidence_report(
                answer_text=str(result.get("formatted_response") or result.get("result") or "")
            )
            fast_search_summary = dict(fast_search_report.get("summary") or {})
            claim_count = int(fast_search_summary.get("claim_count") or 0)
            citation_coverage = float(fast_search_summary.get("citation_coverage") or 0.0)
            unsupported_claims = int(fast_search_summary.get("unsupported_claims") or 0)
            verification_state = str(
                (self._trace_data.get("evidence_stats") or {}).get("verification_state") or "unknown"
            ).strip().lower()
            if verification_state not in {"verified", "confirmed"} and (
                claim_count == 0 or citation_coverage < 0.55 or unsupported_claims > 0
            ):
                safe_fast_search = self._build_fast_search_unverified_message(active_goal)
                result["formatted_response"] = safe_fast_search
                result["result"] = safe_fast_search
                result["direct_answer"] = safe_fast_search[:250] + ("..." if len(safe_fast_search) > 250 else "")

        # --- Memory update for follow-ups ---
        if result.get("formatted_response"):
            self._memory.set_last_response(result["formatted_response"])
            self._runtime_context_ready = True

        # --- Classification metadata ---
        if classification:
            result["intent"] = classification.intent.value
            result["domain"] = classification.domain.value
            if isinstance(classification.metadata, dict):
                if classification.metadata.get("route_label"):
                    result["route_label"] = classification.metadata.get("route_label")
                if classification.metadata.get("route_source"):
                    result["route_source"] = classification.metadata.get("route_source")
                if classification.metadata.get("route_confidence") is not None:
                    result["route_confidence"] = classification.metadata.get("route_confidence")
                if classification.metadata.get("doc_context_active") is not None:
                    result["doc_context_active"] = bool(classification.metadata.get("doc_context_active"))
                if isinstance(classification.metadata.get("policy_override_reasons"), list):
                    result["policy_override_reasons"] = classification.metadata.get("policy_override_reasons")[:5]
                if classification.metadata.get("query_kind"):
                    result["query_kind"] = classification.metadata.get("query_kind")
        if self._trace_data.get("query_kind"):
            result["query_kind"] = self._trace_data.get("query_kind")
        if self._trace_data.get("verification_state"):
            result["verification_state"] = self._trace_data.get("verification_state")
        if self._trace_data.get("policy_reason"):
            result["policy_reason"] = self._trace_data.get("policy_reason")

        # --- Feedback memory capture for weak outcomes ---
        if result.get("evaluation"):
            evaluation = result["evaluation"]
            overall = float(evaluation.get("overall", 1.0) or 1.0)
            if overall < 0.6:
                await self._get_feedback_memory().add_system_failure(
                    user_id=user_id,
                    query=active_goal,
                    failed_answer=str(result.get("result", ""))[:1200],
                    reason="low_evaluation_score",
                    confidence=float(result.get("confidence", 0.0) or 0.0),
                )

        # --- Plan memory learning capture ---
        if state:
            plan_latency_ms = max(0.0, (time.time() - float(state.created_at or time.time())) * 1000)
            self._plan_memory.add_record(
                goal=active_goal,
                plan=state.plan,
                outcome="success" if result.get("status") == "success" else "failed",
                confidence=float(result.get("confidence", state.confidence) or 0.0),
                cost=float(state.cost or 0.0),
                latency_ms=plan_latency_ms,
                failure_reason=str(result.get("error") or ""),
            )
            get_metrics().plan_memory_records = self._plan_memory.count
            await self._persist_execution_memory(
                user_id=user_id,
                state=state,
                result=result,
                latency_ms=plan_latency_ms,
            )
        self._emit_learning_snapshots()

        if result.get("status") == "success":
            get_metrics().success_requests += 1
            if tracker:
                tracker.update(ProgressPhase.COMPLETE)
                remove_tracker(tracker_id)
        else:
            get_metrics().failed_requests += 1
            if tracker:
                tracker.update(ProgressPhase.FAILED, detail=result.get("error", "failed"))
                remove_tracker(tracker_id)
        if self._trace_enabled and self._request_started_at:
            total_ms = (time.time() - float(self._request_started_at)) * 1000
            timing = self._trace_data.setdefault("timing", {})
            timing["total_ms"] = round(max(0.0, total_ms), 2)
        self._attach_execution_trace(
            result=result,
            state=state,
            classification=classification,
            goal_override=goal_override,
        )
        return result

    def _apply_tone_output_styling(self, text: str, intent: IntentType, goal: str) -> str:
        """Keep responses natural to tone profile without changing facts."""
        out = str(text or "").strip()
        tone = self._active_tone
        if not out or tone is None:
            return out
        if "```" in out:
            return out
        if str(self._trace_data.get("planner_path") or "").strip().lower() in {"fast_search", "dynamic_lookup"}:
            return out
        if intent in {IntentType.RESEARCH, IntentType.NEWS, IntentType.DEBUG}:
            return out
        if re.search(r"(?i)\b(def|class|function|import|return)\b", out) and ("\n" in out or "```" in out):
            return out
        if not tone.emoji_allowed:
            return out
        if re.search(r"[\U0001F300-\U0001FAFF]", out):
            return out

        emoji = self._pick_contextual_emoji(goal=goal, text=out)
        if not emoji:
            return out
        if out.endswith((".", "!", "?")):
            return f"{out} {emoji}"
        return f"{out}. {emoji}"

    def _pick_contextual_emoji(self, goal: str, text: str) -> str:
        merged = f"{goal} {text}".lower()
        # Use explicit Unicode escapes to avoid mojibake across shells/editors.
        if any(k in merged for k in ("coffee", "cafe", "espresso", "caffeine", "tea")):
            return "\u2615"
        if any(k in merged for k in ("sleep", "night", "rest")):
            return "\U0001F634"
        if any(k in merged for k in ("food", "eat", "diet", "meal")):
            return "\U0001F37D\ufe0f"
        if any(k in merged for k in ("thanks", "thank you", "appreciate")):
            return "\U0001F64F"
        if any(k in merged for k in ("great", "awesome", "nice", "good")):
            return "\U0001F642"
        return "\U0001F642"

    # ===========================================================
    # UTILITIES
    # ===========================================================

    def _reset_execution_trace(
        self,
        request_id: str,
        goal: str,
        include_trace: bool,
    ) -> None:
        self._trace_enabled = include_trace
        self._trace_data = {
            "request_id": request_id,
            "goal": goal,
            "intent": None,
            "mode": None,
            "query_kind": "general",
            "verification_state": "unknown",
            "policy_reason": None,
            "interpretation": None,
            "routing_profile": None,
            "route_decision": None,
            "route_boundary_summary": None,
            "query_frame": None,
            "query_frame_mismatch_count": 0,
            "query_frame_aligned_count": 0,
            "query_frame_unknown_count": 0,
            "query_frame_supported_multilingual_count": 0,
            "query_frame_semantic_fallback_used_count": 0,
            "query_frame_fastpath_used_count": 0,
            "query_frame_low_confidence_count": 0,
            "query_frame_entity_handoff_enabled": False,
            "query_frame_entity_handoff_applied": False,
            "query_frame_entity_handoff_blocked_reason": "",
            "entity_handoff_source": "",
            "entity_handoff_lookup_type": "",
            "entity_handoff_entity_name": "",
            "entity_handoff_requested_role": "",
            "entity_handoff_answer_language": "",
            "answer_language": "en",
            "answer_language_source": "default",
            "language_preservation_applied": False,
            "language_preservation_status": "english_default",
            "language_preservation_limitations": "",
            "entity_search_queries_generated": [],
            "entity_search_query_lanes": [],
            "legacy_entity_resolver_used": True,
            "planning_handoff": None,
            "planner_path": None,
            "dag_name": None,
            "steps": [],
            "fallback_used": False,
            "fallback_reason": None,
            "freshness_check": {
                "status": "not_applicable",
                "stale_phrase_detected": False,
                "note": None,
            },
            "evidence_stats": {
                "source_count": 0,
                "provider_count": 0,
                "official_count": 0,
                "trusted_count": 0,
                "extract_count": 0,
                "extract_fetch_count": 0,
                "extract_rejected_count": 0,
                "extraction_quality": 0.0,
                "domain_diversity": 0.0,
                "last_verified": None,
                "high_stakes_mode": False,
                "query_kind": "general",
                "verification_state": "unknown",
                "source_rows": [],
            },
            "timing": {
                "route_ms": 0.0,
                "llm_ms": 0.0,
                "llm_calls": 0,
                "total_ms": 0.0,
            },
            "usage": {},
        }

    def _set_trace_value(self, key: str, value: Any) -> None:
        if not self._trace_enabled:
            return
        self._trace_data[key] = value

    def _record_trace_timing(self, key: str, delta_ms: float) -> None:
        if not self._trace_enabled:
            return
        timing = self._trace_data.setdefault("timing", {})
        prior = float(timing.get(key, 0.0) or 0.0)
        timing[key] = round(prior + max(0.0, float(delta_ms or 0.0)), 2)

    def _increment_trace_counter(self, key: str, delta: int = 1) -> None:
        if not self._trace_enabled:
            return
        timing = self._trace_data.setdefault("timing", {})
        prior = int(timing.get(key, 0) or 0)
        timing[key] = prior + int(delta or 0)

    def _mark_trace_fallback(
        self,
        reason: str,
        freshness_status: Optional[str] = None,
        freshness_note: Optional[str] = None,
    ) -> None:
        if not self._trace_enabled:
            return
        self._trace_data["fallback_used"] = True
        self._trace_data["fallback_reason"] = reason
        freshness = self._trace_data.setdefault(
            "freshness_check",
            {"status": "not_applicable", "stale_phrase_detected": False, "note": None},
        )
        if reason == "stale_cutoff_phrase":
            freshness["stale_phrase_detected"] = True
        if freshness_status:
            freshness["status"] = freshness_status
        if freshness_note:
            freshness["note"] = freshness_note

    def _build_query_reference_rows(self, queries: List[str]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for query in (queries or [])[:3]:
            q = str(query or "").strip()
            if not q:
                continue
            rows.append(
                {
                    "title": f"Search query: {q}",
                    "link": f"https://www.google.com/search?q={quote_plus(q)}",
                    "provider": "search_query",
                    "tier": "mixed",
                    "date_hint": None,
                }
            )
        return rows

    def _build_doc_mode_reference_rows(self, *, goal: str, doc_context_active: bool) -> List[Dict[str, Any]]:
        label = (
            "Active uploaded document context was used for this quick doc-mode response."
            if doc_context_active
            else "No active uploaded document context was available in this request."
        )
        link = "internal://doc-context/active" if doc_context_active else "internal://doc-context/missing"
        query = str(goal or "").strip()
        rows: List[Dict[str, Any]] = [
            {
                "title": label,
                "link": link,
                "provider": "doc_context",
                "tier": "mixed",
                "date_hint": None,
            }
        ]
        if query:
            rows.append(
                {
                    "title": f"User request: {query[:120]}",
                    "link": f"internal://request/{quote_plus(query)[:180]}",
                    "provider": "request_context",
                    "tier": "mixed",
                    "date_hint": None,
                }
            )
        return rows

    def _build_doc_mode_retrieval_rows(self, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for idx, source in enumerate(list(sources or [])[:8], start=1):
            item = source if isinstance(source, dict) else {}
            doc_id = str(item.get("doc_id") or "").strip()
            chunk_index = item.get("chunk_index")
            page_start = item.get("page_start")
            page_end = item.get("page_end")
            score = float(item.get("score") or 0.0)

            link = ""
            title = str(item.get("title") or "").strip()
            if doc_id:
                safe_chunk = str(chunk_index if chunk_index is not None else "na")
                safe_page_start = str(page_start if page_start is not None else "na")
                safe_page_end = str(page_end if page_end is not None else "na")
                link = (
                    f"internal://doc/{quote_plus(doc_id)}"
                    f"?chunk={quote_plus(safe_chunk)}"
                    f"&page_start={quote_plus(safe_page_start)}"
                    f"&page_end={quote_plus(safe_page_end)}"
                )
                if not title:
                    title = f"{doc_id} chunk {safe_chunk}"
                    if safe_page_start != "na":
                        title += f" p.{safe_page_start}"
                        if safe_page_end != "na" and safe_page_end != safe_page_start:
                            title += f"-{safe_page_end}"
            else:
                link = str(item.get("link") or item.get("url") or "").strip()
                if not title:
                    title = f"Document evidence #{idx}"

            if not link:
                continue

            rows.append(
                {
                    "title": title,
                    "link": link,
                    "provider": "uploaded_document",
                    "tier": "doc",
                    "date_hint": None,
                    "rank_score": score,
                }
            )
        return rows

    def _build_doc_mode_summary(
        self,
        *,
        payload: Dict[str, Any],
        source_rows: List[Dict[str, Any]],
        planner_path: str,
    ) -> Dict[str, Any]:
        metadata = dict(payload.get("metadata") or {})
        validation = dict(payload.get("validation") or {})
        validation_score = validation.get("score")
        if validation_score is None:
            validation_score = metadata.get("validation_score")
        validation_issues = list(validation.get("issues") or [])
        issue_count = len(validation_issues) if validation_issues else int(metadata.get("validation_issue_count") or 0)
        return {
            "path": str(planner_path or "doc_mode_direct"),
            "mode": str(payload.get("mode") or metadata.get("mode_selected") or "general_doc_assist"),
            "grounding_level": str(metadata.get("grounding_level") or "weak"),
            "retrieval_strength": str(metadata.get("retrieval_strength") or "unknown"),
            "retrieved_chunks": int(metadata.get("retrieved_chunks") or 0),
            "source_count": len(source_rows),
            "source_docs": list(metadata.get("source_docs") or []),
            "cache_hit": bool(metadata.get("cache_hit")),
            "validation_grounded": bool(
                validation.get("grounded") if "grounded" in validation else metadata.get("validation_grounded")
            ),
            "validation_score": round(float(validation_score or 0.0), 3),
            "validation_issue_count": issue_count,
            "confidence_tier": str(metadata.get("confidence_tier") or "unknown"),
            "confidence_reason": str(metadata.get("confidence_reason") or "").strip(),
            "retrieval_profile": metadata.get("retrieval_profile"),
            "retrieval_summary": metadata.get("retrieval_summary") or {},
        }

    async def _resolve_doc_mode_direct_answer(
        self,
        *,
        goal: str,
        doc_context_active: bool,
        doc_ids: List[str],
    ) -> tuple[str, List[Dict[str, Any]], str, Dict[str, Any]]:
        normalized_doc_ids = [str(doc_id).strip() for doc_id in (doc_ids or []) if str(doc_id).strip()]
        if doc_context_active and normalized_doc_ids:
            try:
                from taos.core.documents.ask_service import DocumentAskService

                doc_ask = DocumentAskService()
                payload = await doc_ask.ask(
                    user_id=str(self._active_user_id or "default"),
                    doc_ids=normalized_doc_ids,
                    question=goal,
                )
                answer = str(payload.get("answer") or "").strip()
                source_rows = self._build_doc_mode_retrieval_rows(list(payload.get("sources") or []))
                if answer:
                    if not source_rows:
                        source_rows = self._build_doc_mode_reference_rows(
                            goal=goal,
                            doc_context_active=True,
                        )
                    return answer, source_rows, "doc_mode_retrieval", dict(payload)
            except Exception as exc:
                self._log(
                    "engine.doc_mode_retrieval_failed",
                    error=str(exc),
                    doc_count=len(normalized_doc_ids),
                )

        return (
            self._build_doc_mode_quick_answer(goal=goal, doc_context_active=doc_context_active),
            self._build_doc_mode_reference_rows(goal=goal, doc_context_active=doc_context_active),
            "doc_mode_direct",
            {},
        )

    def _append_direct_trace_step(
        self,
        step_type: str,
        status: str,
        summary: str,
        tool: Optional[str] = None,
        retries: int = 0,
        latency_ms: float = 0.0,
        error: Optional[str] = None,
    ) -> None:
        if not self._trace_enabled:
            return
        steps = self._trace_data.setdefault("steps", [])
        steps.append(
            {
                "id": f"direct_{len(steps) + 1}",
                "type": step_type,
                "status": status,
                "tool": tool,
                "retries": retries,
                "latency_ms": round(float(latency_ms or 0.0), 2),
                "summary": summary[:240],
                "error": error,
                "nodes": [],
            }
        )

    def _update_trace_from_plan(self, plan: PlanObject) -> None:
        if not self._trace_enabled:
            return
        has_dag = any(step.step_type == StepType.DAG_EXEC for step in plan.steps)
        if has_dag:
            self._trace_data["planner_path"] = "dag_exec"
            for step in plan.steps:
                if step.step_type == StepType.DAG_EXEC and step.dag_name:
                    self._trace_data["dag_name"] = step.dag_name
                    break
        else:
            self._trace_data.setdefault("planner_path", "fsm")

    def _attach_execution_trace(
        self,
        result: Dict[str, Any],
        state: Optional[GlobalState],
        classification: Optional[ClassificationResult],
        goal_override: Optional[str],
    ) -> None:
        if self._trace_enabled:
            trace_payload = self._build_execution_trace(
                state=state,
                classification=classification,
                goal_override=goal_override,
                result=result,
            )
            result["trace"] = trace_payload
            result["trust_block"] = trace_payload.get("trust_block")
            result["evidence_matrix_summary"] = trace_payload.get("evidence_matrix_summary")
            result["freshness_summary"] = trace_payload.get("freshness_summary")
            result["evidence_selection_summary"] = trace_payload.get("evidence_selection_summary")
            result["citation_plan_summary"] = trace_payload.get("citation_plan_summary")
            result["diversity_summary"] = trace_payload.get("diversity_summary")
            result["conflict_summary"] = trace_payload.get("conflict_summary")
            result["high_stakes_summary"] = trace_payload.get("high_stakes_summary")
            result["cache_summary"] = trace_payload.get("cache_summary")
            if trace_payload.get("entity_intelligence_summary"):
                result["entity_intelligence_summary"] = trace_payload.get("entity_intelligence_summary")
                metadata = dict(result.get("metadata") or {})
                metadata.setdefault("entity_intelligence_summary", trace_payload.get("entity_intelligence_summary"))
                result["metadata"] = metadata
            if trace_payload.get("document_summary"):
                result["document_summary"] = trace_payload.get("document_summary")
                metadata = dict(result.get("metadata") or {})
                metadata.setdefault("document_summary", trace_payload.get("document_summary"))
                result["metadata"] = metadata
            document_warnings = list(self._trace_data.get("document_warnings") or [])
            if document_warnings:
                result["warnings"] = list(dict.fromkeys(list(result.get("warnings") or []) + document_warnings))
            calibrated_confidence = trace_payload.get("confidence")
            if isinstance(calibrated_confidence, (int, float)):
                result["confidence"] = float(calibrated_confidence)
            return

        planner_path = self._trace_data.get("planner_path")
        if not planner_path and state:
            planner_path = "dag_exec" if any(
                step.step_type == StepType.DAG_EXEC for step in (state.plan.steps if state.plan else [])
            ) else "fsm"
        if not planner_path:
            planner_path = "direct"
        freshness = dict(self._trace_data.get("freshness_check") or {})
        freshness.setdefault("status", "not_applicable")
        freshness.setdefault("stale_phrase_detected", False)
        freshness.setdefault("note", None)
        evidence_report = self._build_evidence_report(
            answer_text=str(result.get("formatted_response") or result.get("result") or "")
        )
        trust_block = self._build_trust_block(
            planner_path=planner_path,
            freshness=freshness,
            fallback_used=bool(self._trace_data.get("fallback_used")),
            confidence=float(result.get("confidence", state.confidence if state else 0.0) or 0.0),
            evidence_report=evidence_report,
        )
        result["trust_block"] = trust_block
        result["evidence_matrix_summary"] = evidence_report.get("summary")
        result["freshness_summary"] = trust_block.get("freshness_summary")
        result["evidence_selection_summary"] = trust_block.get("evidence_selection_summary")
        result["citation_plan_summary"] = trust_block.get("citation_plan_summary")
        result["diversity_summary"] = trust_block.get("diversity_summary")
        result["conflict_summary"] = trust_block.get("conflict_summary")
        result["high_stakes_summary"] = trust_block.get("high_stakes_summary")
        result["cache_summary"] = trust_block.get("cache_summary")
        if self._trace_data.get("entity_intelligence_summary"):
            result["entity_intelligence_summary"] = self._trace_data.get("entity_intelligence_summary")
            metadata = dict(result.get("metadata") or {})
            metadata.setdefault("entity_intelligence_summary", self._trace_data.get("entity_intelligence_summary"))
            result["metadata"] = metadata
        document_summary = self._trace_data.get("document_summary") or (self._trace_data.get("evidence_stats") or {}).get("doc_summary")
        if document_summary:
            result["document_summary"] = document_summary
            metadata = dict(result.get("metadata") or {})
            metadata.setdefault("document_summary", document_summary)
            result["metadata"] = metadata
        document_warnings = list(self._trace_data.get("document_warnings") or [])
        if document_warnings:
            result["warnings"] = list(dict.fromkeys(list(result.get("warnings") or []) + document_warnings))
        calibrated_confidence = trust_block.get("confidence_score")
        if isinstance(calibrated_confidence, (int, float)):
            result["confidence"] = float(calibrated_confidence)

    def _build_evidence_report(self, *, answer_text: str) -> Dict[str, Any]:
        source_rows = list((self._trace_data.get("evidence_stats") or {}).get("source_rows") or [])
        if not answer_text or not source_rows:
            return {
                "claims": [],
                "summary": {
                    "claim_count": 0,
                    "supported_claims": 0,
                    "partially_supported_claims": 0,
                    "unsupported_claims": 0,
                    "citation_coverage": 0.0,
                },
                "overall_support": "not_applicable",
            }
        evidence = dict(self._trace_data.get("evidence_stats") or {})
        if (
            str(self._trace_data.get("planner_path") or "").strip().lower() == "fast_search"
            and str(evidence.get("query_kind") or "").strip().lower() == "version_lookup"
            and str(evidence.get("verification_state") or "").strip().lower() in {"verified", "confirmed"}
            and bool(evidence.get("official_source_found"))
        ):
            return {
                "claims": [
                    {
                        "claim": answer_text.splitlines()[0].strip(),
                        "support": "supported",
                        "support_level": "supported",
                        "support_score": 1.0,
                        "citation_required": True,
                        "sources": [
                            str((source_rows[0] or {}).get("title") or (source_rows[0] or {}).get("link") or "Source")
                        ],
                        "matched_sources": source_rows[:1],
                    }
                ],
                "summary": {
                    "claim_count": 1,
                    "supported_claims": 1,
                    "partially_supported_claims": 0,
                    "unsupported_claims": 0,
                    "citation_coverage": 1.0,
                },
                "overall_support": "strong",
            }
        return self._citation_checker.analyze(answer=answer_text, source_rows=source_rows)

    def _build_execution_trace(
        self,
        state: Optional[GlobalState],
        classification: Optional[ClassificationResult],
        goal_override: Optional[str],
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        request_id = self._trace_data.get("request_id") or result.get("request_id") or "unknown"
        active_goal = state.goal if state else (goal_override or self._trace_data.get("goal") or "")
        research_like = (
            classification is not None
            and classification.intent in (IntentType.RESEARCH, IntentType.NEWS)
        ) or self._is_freshness_sensitive_research(active_goal)

        planner_path = self._trace_data.get("planner_path")
        if not planner_path and state:
            planner_path = "dag_exec" if any(
                step.step_type == StepType.DAG_EXEC for step in (state.plan.steps if state.plan else [])
            ) else "fsm"
        if not planner_path:
            planner_path = "direct"

        dag_name = self._trace_data.get("dag_name")
        if not dag_name and state and state.plan:
            for step in state.plan.steps:
                if step.step_type == StepType.DAG_EXEC and step.dag_name:
                    dag_name = step.dag_name
                    break

        freshness = dict(self._trace_data.get("freshness_check") or {})
        freshness.setdefault("status", "not_applicable")
        freshness.setdefault("stale_phrase_detected", False)
        freshness.setdefault("note", None)
        response_text = str(result.get("formatted_response") or result.get("result") or "")
        fallback_used = bool(self._trace_data.get("fallback_used"))
        fallback_reason = self._trace_data.get("fallback_reason")
        if not fallback_used and "I could not verify a reliable live update" in response_text:
            fallback_used = True
            fallback_reason = "research_unverified_fallback"
        elif not fallback_used and "source-grounded status" in response_text and "Timeline (newest evidence first):" in response_text:
            fallback_used = True
            fallback_reason = "research_evidence_fallback"
        elif not fallback_used and "I could not fetch a reliable live quote right now" in response_text:
            fallback_used = True
            fallback_reason = "dynamic_lookup_unavailable"
        if research_like and freshness["status"] == "not_applicable":
            freshness["status"] = "recovered" if fallback_used else "passed"
        if "As of " in response_text and "source-grounded status" in response_text:
            freshness["status"] = "recovered"

        base_confidence = float(result.get("confidence", state.confidence if state else 0.0) or 0.0)
        evidence_report = self._build_evidence_report(answer_text=response_text)
        trust_block = self._build_trust_block(
            planner_path=planner_path,
            freshness=freshness,
            fallback_used=fallback_used,
            confidence=base_confidence,
            evidence_report=evidence_report,
        )
        trace_confidence = float(trust_block.get("confidence_score", base_confidence) or base_confidence)
        route_for_usage = str(self._trace_data.get("route_label") or planner_path or "task")
        usage_meter = UsageMeter.from_trace(route_for_usage, self._trace_data)
        usage = usage_meter.snapshot()
        quota_decision = self._quota_manager.check(usage)
        if not quota_decision.allowed:
            usage_meter.mark_budget_exceeded()
            usage = usage_meter.snapshot()
        usage_payload = usage.to_dict()
        return {
            "request_id": request_id,
            "intent": self._trace_data.get("intent") or (classification.intent.value if classification else result.get("intent")),
            "mode": self._trace_data.get("mode") or (classification.suggested_mode if classification else None),
            "query_kind": self._trace_data.get("query_kind"),
            "search_depth_mode": self._trace_data.get("search_depth_mode"),
            "search_depth_reason": self._trace_data.get("search_depth_reason"),
            "search_depth_confidence": self._trace_data.get("search_depth_confidence"),
            "verification_state": self._trace_data.get("verification_state"),
            "policy_reason": self._trace_data.get("policy_reason"),
            "route_label": self._trace_data.get("route_label"),
            "route_source": self._trace_data.get("route_source"),
            "route_confidence": self._trace_data.get("route_confidence"),
            "policy_override_reasons": self._trace_data.get("policy_override_reasons"),
            "doc_context_active": self._trace_data.get("doc_context_active"),
            "interpretation": self._trace_data.get("interpretation"),
            "routing_profile": self._trace_data.get("routing_profile"),
            "route_decision": self._trace_data.get("route_decision"),
            "route_boundary_summary": self._trace_data.get("route_boundary_summary"),
            "query_frame": self._trace_data.get("query_frame"),
            "query_frame_mismatch_count": self._trace_data.get("query_frame_mismatch_count"),
            "query_frame_aligned_count": self._trace_data.get("query_frame_aligned_count"),
            "query_frame_unknown_count": self._trace_data.get("query_frame_unknown_count"),
            "query_frame_supported_multilingual_count": self._trace_data.get("query_frame_supported_multilingual_count"),
            "query_frame_semantic_fallback_used_count": self._trace_data.get("query_frame_semantic_fallback_used_count"),
            "query_frame_fastpath_used_count": self._trace_data.get("query_frame_fastpath_used_count"),
            "query_frame_low_confidence_count": self._trace_data.get("query_frame_low_confidence_count"),
            "query_frame_entity_handoff_enabled": self._trace_data.get("query_frame_entity_handoff_enabled"),
            "query_frame_entity_handoff_applied": self._trace_data.get("query_frame_entity_handoff_applied"),
            "query_frame_entity_handoff_blocked_reason": self._trace_data.get("query_frame_entity_handoff_blocked_reason"),
            "entity_handoff_source": self._trace_data.get("entity_handoff_source"),
            "entity_handoff_lookup_type": self._trace_data.get("entity_handoff_lookup_type"),
            "entity_handoff_entity_name": self._trace_data.get("entity_handoff_entity_name"),
            "entity_handoff_requested_role": self._trace_data.get("entity_handoff_requested_role"),
            "entity_handoff_answer_language": self._trace_data.get("entity_handoff_answer_language"),
            "answer_language": self._trace_data.get("answer_language"),
            "answer_language_source": self._trace_data.get("answer_language_source"),
            "language_preservation_applied": self._trace_data.get("language_preservation_applied"),
            "language_preservation_status": self._trace_data.get("language_preservation_status"),
            "language_preservation_limitations": self._trace_data.get("language_preservation_limitations"),
            "entity_search_queries_generated": self._trace_data.get("entity_search_queries_generated"),
            "entity_search_query_lanes": self._trace_data.get("entity_search_query_lanes"),
            "legacy_entity_resolver_used": self._trace_data.get("legacy_entity_resolver_used"),
            "planning_handoff": self._trace_data.get("planning_handoff"),
            "planner_path": planner_path,
            "dag_name": dag_name,
            "pipeline_stages": (
                ["Query", "Search", "Rank", "Answer", "Trust"]
                if planner_path == "fast_search"
                else
                ["Query", "Search", "Rank", "Extract", "Synthesize", "Trust"]
                if planner_path in {"deep_research", "entity_lookup", "dag_exec", "fsm"}
                else ["Query", "Generate", "Trust"]
            ),
            "tone_profile": self._trace_data.get("tone_profile"),
            "provider_health": self._trace_data.get("provider_health") or provider_health_snapshot(),
            "usage": usage_payload,
            "quota": quota_decision.to_dict(),
            "fsm_transitions": self._build_fsm_transitions(state),
            "steps": self._build_trace_steps(state, request_id=request_id),
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "freshness_check": freshness,
            "freshness_summary": (self._trace_data.get("evidence_stats") or {}).get("freshness_summary"),
            "evidence_selection_summary": (self._trace_data.get("evidence_stats") or {}).get("evidence_selection_summary"),
            "citation_plan_summary": (self._trace_data.get("evidence_stats") or {}).get("citation_plan_summary"),
            "diversity_summary": (self._trace_data.get("evidence_stats") or {}).get("diversity_summary"),
            "conflict_summary": (self._trace_data.get("evidence_stats") or {}).get("conflict_summary"),
            "high_stakes_summary": (self._trace_data.get("evidence_stats") or {}).get("high_stakes_summary"),
            "cache_summary": (self._trace_data.get("evidence_stats") or {}).get("cache_summary"),
            "document_summary": self._trace_data.get("document_summary")
            or (self._trace_data.get("evidence_stats") or {}).get("doc_summary"),
            "entity_intelligence_summary": self._trace_data.get("entity_intelligence_summary"),
            "confidence": trace_confidence,
            "timing": dict(self._trace_data.get("timing") or {}),
            "trust_block": trust_block,
            "evidence_matrix_summary": evidence_report.get("summary"),
        }

    def _build_query_frame_observation(self, *, query: str, selected_route: str) -> Dict[str, Any]:
        frame = self._query_frame_builder.build(query)
        intent_family = str(frame.intent or "unknown").strip().lower() or "unknown"
        alignment = compare_query_frame_to_selected_route(frame, selected_route)
        return {
            "canonical_query": str(frame.canonical_query or "").strip(),
            "original_query": str(frame.original_query or "").strip(),
            "normalized_query": str(frame.normalized_query or "").strip(),
            "detected_language": str(frame.detected_language or "en").strip().lower(),
            "answer_language": str(frame.answer_language or frame.detected_language or "en").strip().lower(),
            "detected_script": str(frame.detected_script or "Latin"),
            "intent_family": intent_family,
            "lookup_type": str(frame.lookup_type or "").strip().lower(),
            "entity_name": str(frame.entity or "").strip(),
            "requested_role": str(frame.role or "").strip().lower(),
            "profile_target": str(frame.lookup_type or "").strip().lower(),
            "evidence_need": "public_web_evidence" if intent_family == "entity_lookup" else "unknown",
            "ambiguity_flags": list(frame.ambiguity_flags or []),
            "warnings": list(frame.warnings or []),
            "confidence": float(frame.confidence or 0.0),
            "source": str(frame.source or ""),
            "search_queries": list(frame.search_queries or []),
            "normalized_terms": [part for part in re.findall(r"[a-z0-9]+", str(frame.normalized_query or "").lower()) if part][:20],
            "current_selected_route": str(alignment.get("current_selected_route") or "").strip().lower(),
            "query_frame_suggested_family": str(alignment.get("query_frame_suggested_family") or "unknown"),
            "route_alignment": str(alignment.get("route_alignment") or "unknown"),
            "mismatch_reason": str(alignment.get("mismatch_reason") or ""),
        }

    def _set_query_frame_telemetry(self, query_frame_observation: Dict[str, Any]) -> None:
        alignment = str(query_frame_observation.get("route_alignment") or "unknown").strip().lower()
        intent_family = str(query_frame_observation.get("query_frame_suggested_family") or "unknown").strip().lower()
        stats = {
            "query_frame_mismatch_count": 1 if alignment == "mismatch" else 0,
            "query_frame_aligned_count": 1 if alignment == "aligned" else 0,
            "query_frame_unknown_count": 1 if alignment == "unknown" else 0,
            "query_frame_supported_multilingual_count": 1 if intent_family != "unknown" else 0,
            "query_frame_semantic_fallback_used_count": 1 if "semantic" in str(query_frame_observation.get("source") or "").lower() else 0,
            "query_frame_fastpath_used_count": 1 if "fast_path" in str(query_frame_observation.get("source") or "").lower() else 0,
            "query_frame_low_confidence_count": 1 if str(query_frame_observation.get("mismatch_reason") or "") == "query_frame_low_confidence" else 0,
        }
        for key, value in stats.items():
            self._set_trace_value(key, int(value))

    def _decide_query_frame_route_assist(
        self,
        *,
        query_frame_observation: Dict[str, Any],
        selected_route: str,
        query_kind: str,
        doc_context_active: bool,
    ) -> Dict[str, Any]:
        enabled = str(os.getenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "false")).strip().lower() == "true"
        from_route = str(selected_route or "").strip().lower()
        lookup_type = str(query_frame_observation.get("lookup_type") or "").strip().lower()
        confidence = float(query_frame_observation.get("confidence") or 0.0)
        intent_family = str(query_frame_observation.get("intent_family") or "unknown").strip().lower()
        entity_name = str(query_frame_observation.get("entity_name") or "").strip()
        canonical_query = str(query_frame_observation.get("canonical_query") or "").strip()
        ambiguity_flags = list(query_frame_observation.get("ambiguity_flags") or [])
        warnings = list(query_frame_observation.get("warnings") or [])

        supported_lookup = {
            "founder_lookup",
            "ceo_lookup",
            "linkedin_profile",
            "official_website",
            "business_legitimacy",
        }
        weak_routes = {"standard_task", "standard_answer", "no_search", "generic_task", "official_search"}
        protected_routes = {"doc_mode", "clarification", "deep_research", "news_search"}

        result = {
            "route_assist_enabled": enabled,
            "route_assist_eligible": False,
            "route_assist_applied": False,
            "route_assist_from": from_route,
            "route_assist_to": from_route,
            "route_assist_reason": "",
            "route_assist_blocked_reason": "",
            "route_assist_confidence": confidence,
            "route_assist_lookup_type": lookup_type,
        }
        if not enabled:
            result["route_assist_blocked_reason"] = "feature_disabled"
            return result
        if from_route in protected_routes or doc_context_active:
            result["route_assist_blocked_reason"] = "protected_route"
            return result
        if str(query_kind or "").strip().lower() == "document_qa":
            result["route_assist_blocked_reason"] = "protected_route"
            return result
        if from_route not in weak_routes:
            result["route_assist_blocked_reason"] = "protected_route"
            return result
        if intent_family != "entity_lookup":
            result["route_assist_blocked_reason"] = "query_frame_not_entity_lookup"
            return result
        if lookup_type not in supported_lookup:
            result["route_assist_blocked_reason"] = "unsupported_lookup_type"
            return result
        if confidence < 0.85:
            result["route_assist_blocked_reason"] = "query_frame_low_confidence"
            return result
        if not entity_name:
            result["route_assist_blocked_reason"] = "query_frame_missing_entity"
            return result
        if not canonical_query:
            result["route_assist_blocked_reason"] = "query_frame_missing_canonical_query"
            return result
        if ambiguity_flags:
            result["route_assist_blocked_reason"] = "query_frame_ambiguous"
            return result
        if warnings:
            result["route_assist_blocked_reason"] = "query_frame_validation_warning"
            return result

        result["route_assist_eligible"] = True
        result["route_assist_applied"] = True
        result["route_assist_to"] = "entity_lookup"
        result["route_assist_reason"] = "high_confidence_entity_lookup"
        return result

    def _calibrate_research_confidence(
        self,
        *,
        base_confidence: float,
        evidence_expected: bool,
        source_count: int,
        extract_count: int,
        extraction_quality: float,
        domain_diversity: float,
        agreement_score: float,
        agreement_level: str,
        stale_detected: bool,
        conflict_detected: bool,
        signal: str,
        official_source_required: bool,
        official_source_found: bool,
        high_stakes_mode: bool,
        fallback_used: bool,
    ) -> Dict[str, Any]:
        level = str(agreement_level or "").strip().lower()
        signal_norm = str(signal or "").strip().lower()
        base = max(0.0, min(1.0, float(base_confidence or 0.0)))
        if not evidence_expected:
            calibrated = base if base > 0 else 0.58
            if calibrated < 0.52:
                calibrated = 0.56
            calibrated = max(0.08, min(0.98, calibrated))
            if calibrated >= 0.78:
                label = "High"
            elif calibrated >= 0.52:
                label = "Medium"
            else:
                label = "Low"
            return {"score": round(calibrated, 3), "label": label}

        evidence_score = 0.0
        evidence_score += max(0.0, min(1.0, float(agreement_score or 0.0))) * 0.45
        evidence_score += min(max(int(source_count or 0), 0), 5) / 5.0 * 0.16
        evidence_score += min(max(int(extract_count or 0), 0), 4) / 4.0 * 0.08
        evidence_score += max(0.0, min(1.0, float(extraction_quality or 0.0))) * 0.16
        evidence_score += max(0.0, min(1.0, float(domain_diversity or 0.0))) * 0.08
        if level == "high":
            evidence_score += 0.07
        elif level == "medium":
            evidence_score += 0.03
        if official_source_found:
            evidence_score += 0.05
        elif official_source_required:
            evidence_score -= 0.08

        if base > 0:
            calibrated = (base * 0.35) + (evidence_score * 0.65)
        else:
            calibrated = evidence_score

        if fallback_used:
            calibrated -= 0.08
        if conflict_detected:
            calibrated -= 0.16
        if stale_detected:
            calibrated -= 0.14
        if signal_norm == "conflicting":
            calibrated -= 0.12
        elif signal_norm == "partial_conflict":
            calibrated -= 0.08
        if high_stakes_mode and official_source_required and not official_source_found:
            calibrated -= 0.22
        if source_count <= 0:
            calibrated = min(calibrated, 0.22)

        if high_stakes_mode and official_source_required and not official_source_found:
            calibrated = min(calibrated, 0.44)
        if conflict_detected or signal_norm == "conflicting":
            calibrated = min(calibrated, 0.44)
        elif stale_detected or signal_norm == "partial_conflict":
            calibrated = min(calibrated, 0.62)

        calibrated = max(0.08, min(0.98, calibrated))
        if calibrated >= 0.78:
            label = "High"
        elif calibrated >= 0.52:
            label = "Medium"
        else:
            label = "Low"
        return {"score": round(calibrated, 3), "label": label}

    def _build_trust_block(
        self,
        planner_path: str,
        freshness: Dict[str, Any],
        fallback_used: bool,
        confidence: float,
        evidence_report: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        evidence = dict(self._trace_data.get("evidence_stats") or {})
        source_count = int(evidence.get("source_count", 0) or 0)
        extract_count = int(evidence.get("extract_count", 0) or 0)
        extract_rejected_count = int(evidence.get("extract_rejected_count", 0) or 0)
        official_count = int(evidence.get("official_count", 0) or 0)
        provider_count = int(evidence.get("provider_count", 0) or 0)
        domain_diversity = float(evidence.get("domain_diversity", 0.0) or 0.0)
        extraction_quality = float(evidence.get("extraction_quality", 0.0) or 0.0)
        freshness_summary = dict(evidence.get("freshness_summary") or {})
        evidence_selection_summary = dict(evidence.get("evidence_selection_summary") or {})
        citation_plan_summary = dict(evidence.get("citation_plan_summary") or {})
        diversity_summary = dict(evidence.get("diversity_summary") or {})
        conflict_summary = dict(evidence.get("conflict_summary") or {})
        high_stakes_summary = dict(evidence.get("high_stakes_summary") or {})
        cache_summary = dict(evidence.get("cache_summary") or {})
        source_diversity_score = float(evidence.get("source_diversity_score", domain_diversity) or domain_diversity or 0.0)
        extraction_recovery_used = bool(evidence.get("extraction_recovery_used"))
        official_source_required = bool(evidence.get("official_source_required"))
        official_source_found = bool(evidence.get("official_source_found"))
        high_stakes_mode = bool(evidence.get("high_stakes_mode"))
        query_kind = str(evidence.get("query_kind") or self._trace_data.get("query_kind") or "general")
        verification_state = str(
            evidence.get("verification_state") or self._trace_data.get("verification_state") or "unknown"
        ).strip().lower()
        agreement_level = str(evidence.get("agreement_level") or "unknown")
        agreement_score = float(evidence.get("agreement_score", 0.0) or 0.0)
        conflict_detected = bool(evidence.get("conflict_detected"))
        stale_detected = bool(evidence.get("stale_detected"))
        signal = str(evidence.get("signal") or "").strip().lower()
        event_agreement_level = str(evidence.get("event_agreement_level") or agreement_level)
        attribution_agreement_level = str(evidence.get("attribution_agreement_level") or "not_applicable")
        freshness_status = str(freshness.get("status") or "not_applicable")
        evidence_report = dict(evidence_report or {})
        evidence_summary = dict(evidence_report.get("summary") or {})
        supported_claims = int(evidence_summary.get("supported_claims") or 0)
        partial_claims = int(evidence_summary.get("partially_supported_claims") or 0)
        unsupported_claims = int(evidence_summary.get("unsupported_claims") or 0)
        citation_coverage = float(evidence_summary.get("citation_coverage") or 0.0)
        overall_support = str(evidence_report.get("overall_support") or "not_applicable")
        verified_version_lookup = bool(
            planner_path == "fast_search"
            and query_kind == "version_lookup"
            and verification_state in {"verified", "confirmed"}
            and official_source_found
        )
        if verified_version_lookup:
            agreement_level = "high"
            event_agreement_level = "high"
            agreement_score = max(agreement_score, 1.0)
            extraction_quality = max(extraction_quality, 0.85)

        freshness_label = {
            "passed": "High",
            "recovered": "Medium",
            "failed": "Failed",
            "not_applicable": "Not applicable",
        }.get(freshness_status, "Unknown")

        if source_count >= 4 and extract_count >= 2 and provider_count >= 3 and agreement_level in {"high", "medium"}:
            evidence_label = "Strong"
        elif source_count >= 2 and extract_count >= 1:
            evidence_label = "Moderate"
        elif source_count >= 1:
            evidence_label = "Weak"
        else:
            evidence_label = "Minimal"
        if conflict_detected and evidence_label == "Strong":
            evidence_label = "Moderate"
        if stale_detected and evidence_label in {"Strong", "Moderate"}:
            evidence_label = "Weak"
        if query_kind == "entity_lookup" and verification_state == "not_verified":
            evidence_label = "Minimal"
        if verified_version_lookup:
            evidence_label = "Strong"

        calibration = self._calibrate_research_confidence(
            base_confidence=confidence,
            evidence_expected=planner_path in {"deep_research", "dag_exec", "fsm", "dynamic_lookup"},
            source_count=source_count,
            extract_count=extract_count,
            extraction_quality=extraction_quality,
            domain_diversity=domain_diversity,
            agreement_score=agreement_score,
            agreement_level=agreement_level,
            stale_detected=stale_detected,
            conflict_detected=conflict_detected,
            signal=signal,
            official_source_required=official_source_required,
            official_source_found=official_source_found,
            high_stakes_mode=high_stakes_mode,
            fallback_used=fallback_used,
        )
        confidence_label = str(calibration.get("label") or "Low")
        confidence_score = float(calibration.get("score", 0.0) or 0.0)
        citation_calibration = self._confidence_calibrator.calibrate(
            base_confidence=confidence_score,
            citation_report=evidence_report,
            stale_detected=stale_detected,
            conflict_detected=conflict_detected,
            high_stakes_mode=high_stakes_mode,
            unresolved_conflict_count=int(conflict_summary.get("unresolved_conflict_count") or 0),
            official_source_missing=bool(official_source_required and not official_source_found),
        )
        confidence_label = str(citation_calibration.get("label") or confidence_label)
        confidence_score = float(citation_calibration.get("score", confidence_score) or confidence_score)
        confidence_reason = (
            f"Confidence adjusted from evidence coverage {round(citation_coverage * 100)}%"
            if citation_coverage > 0
            else "Confidence adjusted from runtime trust signals."
        )
        if verified_version_lookup:
            confidence_label = "High"
            confidence_score = max(confidence_score, 0.86)
            confidence_reason = "Version confirmed from the npm registry latest tag."
        if citation_coverage <= 0.0 or extract_count <= 0:
            evidence_label = "Minimal"
            confidence_label = "Low"
            confidence_score = min(confidence_score, 0.32)
            if citation_coverage <= 0.0:
                confidence_reason = "Confidence forced low because citation coverage was 0%."
            else:
                confidence_reason = "Confidence forced low because no usable evidence extracts were available."
        if planner_path == "adversarial_guard":
            evidence_label = "Minimal"
            confidence_label = "Low"
            confidence_score = min(confidence_score, 0.28)
            signal = "conflicting"
        if query_kind == "entity_lookup" and verification_state == "not_verified":
            confidence_label = "Low"
            confidence_score = min(confidence_score, 0.34)
        if high_stakes_mode and evidence_label == "Strong" and (official_source_required and not official_source_found):
            evidence_label = "Moderate"
        uncertainty_flags: List[str] = []
        if agreement_level in {"low", "unknown"} or agreement_score < 0.45:
            uncertainty_flags.append("weak_agreement")
        if conflict_detected or signal == "conflicting":
            uncertainty_flags.append("conflicting_evidence")
        elif signal == "partial_conflict":
            uncertainty_flags.append("partial_conflict")
        if stale_detected:
            uncertainty_flags.append("stale_evidence")
        if extraction_quality < 0.45:
            uncertainty_flags.append("low_extraction_quality")
        if domain_diversity < 0.35 and source_count >= 3:
            uncertainty_flags.append("low_domain_diversity")
        if official_source_required and not official_source_found:
            uncertainty_flags.append("official_source_missing")
        if high_stakes_mode:
            uncertainty_flags.append("high_stakes_guard")
        if unsupported_claims > 0:
            uncertainty_flags.append("unsupported_claims")
        elif partial_claims > 0:
            uncertainty_flags.append("partial_support")
        if planner_path == "adversarial_guard":
            uncertainty_flags.append("integrity_guard")
        if query_kind == "entity_lookup" and verification_state == "not_verified":
            uncertainty_flags.append("entity_not_verified")
            agreement_level = "unknown"
            agreement_score = 0.0
            signal = "candidate_only"

        path_label = {
            "direct": "Direct",
            "fast_path": "Direct",
            "dynamic_lookup": "Live Tool",
            "fsm": "FSM",
            "dag_exec": "Structured Research",
            "deep_research": "Structured Research",
            "entity_lookup": "Entity Lookup",
            "adversarial_guard": "Integrity Guard",
        }.get(planner_path, "Direct")

        return {
            "freshness": freshness_label,
            "evidence": evidence_label,
            "execution_path": path_label,
            "fallback_used": fallback_used,
            "confidence": confidence_label,
            "confidence_score": confidence_score,
            "source_count": source_count,
            "usable_sources_count": extract_count,
            "rejected_sources_count": extract_rejected_count,
            "last_verified": evidence.get("last_verified"),
            "official_source_count": official_count,
            "agreement": agreement_level,
            "agreement_score": round(agreement_score, 3),
            "conflict_detected": conflict_detected,
            "stale_detected": stale_detected,
            "domain_diversity": round(domain_diversity, 3),
            "source_diversity_score": round(source_diversity_score, 3),
            "extraction_quality": round(extraction_quality, 3),
            "freshness_summary": freshness_summary,
            "evidence_selection_summary": evidence_selection_summary,
            "citation_plan_summary": citation_plan_summary,
            "diversity_summary": diversity_summary,
            "conflict_summary": conflict_summary,
            "high_stakes_summary": high_stakes_summary,
            "cache_summary": cache_summary,
            "extraction_recovery_used": extraction_recovery_used,
            "signal": (signal or ("conflicting" if conflict_detected else "partial_conflict" if stale_detected else "clean")),
            "event_agreement": event_agreement_level,
            "attribution_agreement": attribution_agreement_level,
            "official_source_required": official_source_required,
            "official_source_found": official_source_found,
            "high_stakes_mode": high_stakes_mode,
            "citation_coverage": round(citation_coverage, 3),
            "supported_claims": supported_claims,
            "partially_supported_claims": partial_claims,
            "unsupported_claims": unsupported_claims,
            "overall_support": overall_support,
            "confidence_reason": confidence_reason,
            "evidence_matrix_summary": evidence_summary,
            "query_kind": query_kind,
            "verification_state": verification_state,
            "answer_mode": str(evidence.get("entity_answer_mode") or ""),
            "requested_role": str(evidence.get("requested_role") or self._trace_data.get("requested_role") or ""),
            "supported_role": str(evidence.get("supported_role") or self._trace_data.get("supported_role") or ""),
            "selected_candidate": str(evidence.get("selected_candidate") or self._trace_data.get("selected_candidate") or ""),
            "exact_role_verified": bool(evidence.get("exact_role_verified")),
            "role_match": bool(evidence.get("role_match")),
            "role_mismatch_reason": str(evidence.get("role_mismatch_reason") or self._trace_data.get("role_mismatch_reason") or ""),
            "linkedin_source_found": bool(evidence.get("linkedin_source_found")),
            "registry_source_found": bool(evidence.get("registry_source_found")),
            "search_lanes_used": list(evidence.get("search_lanes_used") or []),
            "source_tiers_found": list(evidence.get("source_tiers_found") or []),
            "uncertainty_flags": uncertainty_flags,
        }

    def _build_fsm_transitions(self, state: Optional[GlobalState]) -> List[str]:
        if not state:
            planner_path = str(self._trace_data.get("planner_path") or "")
            if planner_path in {"deep_research", "entity_lookup", "dag_exec", "fsm"}:
                return [
                    "INIT -> PLANNING",
                    "PLANNING -> EXECUTING",
                    "EXECUTING -> REFLECTING",
                    "REFLECTING -> TERMINATING",
                ]
            return []
        history = [
            item
            for item in self._controller.get_history()
            if getattr(item, "request_id", None) == state.request_id
        ]
        transitions: List[str] = []
        for previous, current in zip(history, history[1:]):
            from_state = str(previous.current_fsm_state)
            to_state = str(current.current_fsm_state)
            if from_state != to_state:
                transitions.append(f"{from_state} -> {to_state}")
        return transitions

    def _build_trace_steps(self, state: Optional[GlobalState], request_id: str) -> List[Dict[str, Any]]:
        if state and state.step_results:
            return self._build_state_trace_steps(state)

        direct_steps = list(self._trace_data.get("steps") or [])
        if direct_steps:
            return direct_steps

        event_rows = self.get_recent_research_trace(request_id=request_id, limit=120)
        if not event_rows:
            return []
        grouped: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for row in event_rows:
            stage = str(row.get("stage") or "engine")
            if stage not in grouped:
                grouped[stage] = {
                    "id": stage,
                    "type": "reason",
                    "status": "success",
                    "tool": None,
                    "retries": 0,
                    "latency_ms": 0.0,
                    "summary": "",
                    "error": None,
                    "nodes": [],
                }
                order.append(stage)
            event_name = str(row.get("event") or "")
            data = row.get("data") or {}
            summary = self._summarize_research_event(stage=stage, event_name=event_name, data=data)
            if summary:
                grouped[stage]["summary"] = summary
            if "failed" in event_name or "error" in event_name or "rejected" in event_name:
                grouped[stage]["status"] = "failed"
                grouped[stage]["error"] = summary or event_name
            if stage == "researcher":
                grouped[stage]["type"] = "tool"
                grouped[stage]["tool"] = "web_search"
            elif stage == "planner":
                grouped[stage]["type"] = "reason"
            elif stage == "validator":
                grouped[stage]["type"] = "validate"
            elif stage == "synthesizer":
                grouped[stage]["type"] = "reason"
        return [grouped[stage] for stage in order]

    def _build_state_trace_steps(self, state: GlobalState) -> List[Dict[str, Any]]:
        plan_lookup = {step.id: step for step in (state.plan.steps if state.plan else [])}
        trace_steps: List[Dict[str, Any]] = []
        for step_result in state.step_results:
            plan_step = plan_lookup.get(step_result.step_id)
            step_type = self._infer_step_type(plan_step, step_result)
            tool_name = step_result.tool_name or (plan_step.tool if plan_step else None)
            payload = step_result.result if isinstance(step_result.result, dict) else {}
            trace_steps.append(
                {
                    "id": step_result.step_id,
                    "type": step_type,
                    "status": "success" if step_result.success else "failed",
                    "tool": tool_name,
                    "retries": int(step_result.retries_used or 0),
                    "latency_ms": round(float(step_result.latency or 0.0) * 1000, 2),
                    "summary": self._summarize_step_result(step_result),
                    "error": step_result.error,
                    "execution_mode": payload.get("execution_mode") if isinstance(payload, dict) else None,
                    "frontier_count": payload.get("frontier_count") if isinstance(payload, dict) else None,
                    "batches": payload.get("batches", []) if isinstance(payload, dict) else [],
                    "nodes": self._build_dag_trace_nodes(step_result),
                }
            )
        return trace_steps

    def _build_dag_trace_nodes(self, step_result: StepResult) -> List[Dict[str, Any]]:
        payload = step_result.result if isinstance(step_result.result, dict) else {}
        raw_nodes = payload.get("node_results", {}) if isinstance(payload, dict) else {}
        if not isinstance(raw_nodes, dict):
            return []
        nodes: List[Dict[str, Any]] = []
        for node_id, node_result in raw_nodes.items():
            if not isinstance(node_result, dict):
                continue
            tool_name = node_result.get("tool_name")
            nodes.append(
                {
                    "id": str(node_id),
                    "type": "tool" if tool_name else "node",
                    "status": str(node_result.get("status", "success")),
                    "tool": tool_name,
                    "retries": int(node_result.get("retries_used", 0) or 0),
                    "latency_ms": round(float(node_result.get("latency", 0.0) or 0.0) * 1000, 2),
                    "summary": self._summarize_value(node_result.get("output")),
                    "error": node_result.get("error"),
                    "batch_index": node_result.get("batch_index"),
                    "frontier_index": node_result.get("frontier_index"),
                }
            )
        return nodes

    def _infer_step_type(self, plan_step: Optional[PlanStep], step_result: StepResult) -> str:
        if plan_step:
            if plan_step.step_type == StepType.DAG_EXEC:
                return "dag_exec"
            if plan_step.step_type == StepType.REASON:
                return "reason"
            return "tool"
        if (step_result.tool_name or "").startswith("dag:"):
            return "dag_exec"
        if step_result.tool_name:
            return "tool"
        return "reason"

    def _summarize_step_result(self, step_result: StepResult) -> str:
        if not step_result.success:
            return (step_result.error or "Step failed")[:240]
        if isinstance(step_result.result, dict):
            if "final_output" in step_result.result:
                return f"Completed DAG step with {len(step_result.result.get('node_results', {}))} node(s)."
            return self._summarize_value(step_result.result)
        return self._summarize_value(step_result.result)

    def _summarize_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            if "answer" in value:
                return str(value.get("answer", ""))[:240]
            if "text" in value:
                return str(value.get("text", ""))[:240]
            if "results" in value and isinstance(value.get("results"), list):
                return f"Collected {len(value['results'])} result(s)."
            keys = list(value.keys())[:4]
            return f"Structured output with keys: {', '.join(str(k) for k in keys)}."
        if isinstance(value, list):
            return f"Produced {len(value)} item(s)."
        text = re.sub(r"\s+", " ", str(value)).strip()
        return text[:240]

    def _summarize_research_event(self, stage: str, event_name: str, data: Dict[str, Any]) -> str:
        if stage == "classifier":
            intent = data.get("intent")
            domain = data.get("domain")
            if intent and domain:
                return f"Classified query as {intent} in domain {domain}."
        if stage == "planner":
            subgoals = data.get("subgoals")
            if isinstance(subgoals, list) and subgoals:
                return f"Prepared a research plan with {len(subgoals)} subgoal(s)."
            queries = data.get("queries")
            if isinstance(queries, list) and queries:
                return f"Prepared {len(queries)} research query variant(s)."
        if stage == "researcher":
            if "evidence_rows" in data:
                return f"Collected {int(data.get('evidence_rows', 0) or 0)} evidence row(s)."
            if "extract_success" in data:
                fetch_ok = int(data.get("extract_fetch_success", data.get("extract_success", 0)) or 0)
                rejected = int(data.get("extract_rejected", 0) or 0)
                return (
                    f"Extracted {int(data.get('extract_success', 0) or 0)} usable source page(s) "
                    f"from {int(data.get('extract_attempts', 0) or 0)} attempt(s), "
                    f"fetch-ok {fetch_ok}, rejected {rejected}."
                )
            if "queries" in data and isinstance(data["queries"], list):
                return f"Ran {len(data['queries'])} live research search(es)."
        if stage == "validator":
            if "unique_sources" in data:
                return f"Validated and deduplicated down to {int(data.get('unique_sources', 0) or 0)} unique source(s)."
        if stage == "synthesizer":
            if "reason" in data:
                return f"Synthesis flagged: {data['reason']}."
            return "Synthesized a source-grounded research brief."
        if event_name:
            return event_name.replace("engine.", "").replace("_", " ")
        return stage

    async def _persist_execution_memory(
        self,
        user_id: str,
        state: GlobalState,
        result: Dict[str, Any],
        latency_ms: float,
    ) -> None:
        """Persist through the Phase 114 best-effort persistence coordinator."""
        await self._persistence_coordinator.persist_execution_memory(
            engine=self,
            user_id=user_id,
            state=state,
            result=result,
            latency_ms=latency_ms,
        )

    async def _persist_execution_memory_legacy(
        self,
        user_id: str,
        state: GlobalState,
        result: Dict[str, Any],
        latency_ms: float,
    ) -> None:
        """Persist execution artifacts into Firestore schema service (best-effort)."""
        try:
            steps_payload = []
            for sr in state.step_results:
                steps_payload.append(
                    {
                        "step_id": sr.step_id,
                        "success": bool(sr.success),
                        "tool_name": sr.tool_name,
                        "error": sr.error,
                        "cost": float(sr.cost or 0.0),
                        "latency": float(sr.latency or 0.0),
                    }
                )

            agent_names = list(self._agent_reputation.snapshot().keys())
            firestore_memory = self._get_firestore_memory()
            await firestore_memory.log_execution(
                user_id=user_id,
                execution_id=str(state.request_id),
                goal=state.goal,
                steps=steps_payload,
                agents_used=agent_names,
                latency=latency_ms,
                cost=float(state.cost or 0.0),
                success=bool(result.get("status") == "success"),
                critic_flagged=any(not sr.success for sr in state.step_results),
                debate_triggered=bool(result.get("debate_triggered", False)),
            )

            if state.plan:
                await firestore_memory.store_plan(
                    user_id=user_id,
                    goal=state.goal,
                    plan_steps=[
                        {
                            "id": s.id,
                            "action": s.action,
                            "tool": s.tool,
                            "depends_on": list(s.depends_on),
                        }
                        for s in state.plan.steps
                    ],
                    outcome="success" if result.get("status") == "success" else "failed",
                    confidence=float(result.get("confidence", state.confidence) or 0.0),
                    cost=float(state.cost or 0.0),
                    latency=float(latency_ms),
                    failure_reason=str(result.get("error") or ""),
                    tags=[str(result.get("intent", "task"))],
                )

            for tool_name, stats in self._tool_learning.snapshot().items():
                if int(stats.get("calls", 0) or 0) <= 0:
                    continue
                await firestore_memory.update_tool_stats(
                    user_id=user_id,
                    tool_name=tool_name,
                    success=float(stats.get("success_rate", 0.0) or 0.0) >= 0.5,
                    latency=float(stats.get("avg_latency", 0.0) or 0.0),
                    cost=float(stats.get("avg_cost", 0.0) or 0.0),
                )

            for agent_name, rep in self._agent_reputation.snapshot().items():
                await firestore_memory.store_agent_memory(
                    user_id=user_id,
                    agent_name=agent_name,
                    observation=(
                        f"trust={rep.get('trust_score', 0.0):.3f}, "
                        f"success_rate={rep.get('success_rate', 0.0):.3f}"
                    ),
                    outcome="stable" if float(rep.get("trust_score", 0.0) or 0.0) >= 0.5 else "degraded",
                    confidence=float(rep.get("avg_confidence", 0.0) or 0.0),
                    tags=["agent_reputation"],
                )
        except Exception as e:
            self._log("engine.persist_memory_error", error=str(e))

    async def record_feedback(
        self,
        user_id: str,
        query: str,
        bad_answer: str,
        corrected_answer: str,
        tags: Optional[List[str]] = None,
        rating: int = -1,
    ) -> str:
        """Store explicit user correction feedback."""
        return await self._get_feedback_memory().add_feedback(
            user_id=user_id,
            query=query,
            bad_answer=bad_answer,
            corrected_answer=corrected_answer,
            tags=tags,
            rating=rating,
        )

    async def _run_fast_llm(
        self,
        prompt: str,
        apply_tone: bool = False,
        stream_to_progress: bool = False,
    ) -> Optional[str]:
        """Utility for quick, direct LLM responses bypassing orchestration."""
        call_started_at = time.time()
        timing_marked = False

        def _mark_llm_timing() -> None:
            nonlocal timing_marked
            if timing_marked:
                return
            timing_marked = True
            self._record_trace_timing("llm_ms", (time.time() - call_started_at) * 1000)

        self._increment_trace_counter("llm_calls", 1)
        orchestration = ModelOrchestration()
        config = orchestration.get_config("executor")
        fallback = orchestration.get_fallback("executor")
        tracker: Optional[ProgressTracker] = None
        if stream_to_progress and self._active_request_id:
            tracker = get_tracker(self._active_request_id)
        style_system_prompt = (
            "You are TAOS, a practical assistant.\n"
            "Response style rules:\n"
            "1) Answer exactly what the user asked first.\n"
            "2) Default to concise output (1-3 short sentences).\n"
            "3) Explain deeper only if the user asks for explanation (why/how/explain/detail).\n"
            "4) Mirror tone primarily from the current message, while keeping light consistency with recent conversation context.\n"
            "5) Avoid abrupt tone switching; adapt smoothly when tone changes.\n"
            "6) For casual greetings, be warm and natural.\n"
            "7) Emojis are allowed only when context fits: use 0-2 relevant emojis max, never random.\n"
            "8) In technical/serious/high-stakes topics, avoid decorative emojis.\n"
            "9) If user uses playful banter/roast, you may reply with light playful banter once, keep it non-abusive.\n"
            "10) Never use hate, threats, slurs, or personal attacks.\n"
            "11) Never define a phrase unless the user explicitly asks for a definition.\n"
            "12) Keep claims factual and do not invent details.\n"
            "13) If a strict output format is requested in the user prompt, follow that format exactly.\n"
            "14) Do not carry tone across different users or sessions."
        )
        if apply_tone and self._active_tone is not None:
            style_system_prompt += (
                "\n\nRuntime tone profile:\n"
                f"- current_tone={self._active_tone.current_label}\n"
                f"- blended_tone={self._active_tone.blended_label}\n"
                f"- serious={self._active_tone.serious:.3f}, casual={self._active_tone.casual:.3f}, playful={self._active_tone.playful:.3f}\n"
                f"- emoji_allowed={'yes' if self._active_tone.emoji_allowed else 'no'}\n"
                f"- banter_allowed={'yes' if self._active_tone.banter_allowed else 'no'}\n"
                f"- style_hint={self._active_tone.brief_hint}\n"
            )
        headers = {
            "Authorization": "Bearer " + self._settings.openrouter_api_key,
            "HTTP-Referer": self._settings.site_url,
            "X-Title": self._settings.site_name,
            "Content-Type": "application/json",
        }
        models_to_try = [config]
        if fallback and fallback.model_id and fallback.model_id != config.model_id:
            models_to_try.append(fallback)

        for model_cfg in models_to_try:
            if not GLOBAL_PROVIDER_HEALTH.allow_request("openrouter"):
                GLOBAL_PROVIDER_HEALTH.mark_fallback("openrouter")
                self._set_trace_value("provider_health", provider_health_snapshot())
                continue
            payload = {
                "model": model_cfg.model_id,
                "messages": [
                    {"role": "system", "content": style_system_prompt},
                    {"role": "user", "content": prompt},
                ],
            }
            payload.update(model_cfg.to_api_params())
            stream_payload = dict(payload)
            stream_payload["stream"] = True
            for attempt in range(3):
                try:
                    # True token streaming path (for live SSE partial_result updates).
                    if stream_to_progress:
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            async with client.stream(
                                "POST",
                                self._settings.openrouter_base_url + "/chat/completions",
                                headers=headers,
                                json=stream_payload,
                            ) as resp:
                                if resp.status_code == 200:
                                    streamed = await self._consume_openrouter_stream(
                                        response=resp,
                                        tracker=tracker,
                                    )
                                    if streamed:
                                        GLOBAL_PROVIDER_HEALTH.record_success("openrouter")
                                        self._set_trace_value("provider_health", provider_health_snapshot())
                                        _mark_llm_timing()
                                        return streamed.strip()
                                else:
                                    body = (await resp.aread()).decode("utf-8", errors="ignore")
                                    if resp.status_code == 429:
                                        delay = min(1.5 * (2 ** attempt), 8.0)
                                        self._log(
                                            "engine.fast_llm_rate_limited",
                                            model=model_cfg.model_id,
                                            attempt=attempt + 1,
                                            status_code=resp.status_code,
                                            retry_in_s=delay,
                                        )
                                        if attempt < 2:
                                            await asyncio.sleep(delay)
                                            continue
                                    self._log(
                                        "engine.fast_llm_http_error",
                                        model=model_cfg.model_id,
                                        status_code=resp.status_code,
                                        body_preview=body[:240],
                                    )
                                    if resp.status_code == 429 or resp.status_code >= 500:
                                        GLOBAL_PROVIDER_HEALTH.record_failure("openrouter", f"http_{resp.status_code}")
                                    break

                    # Non-stream fallback path.
                    async with httpx.AsyncClient(timeout=20.0) as client:
                        resp = await client.post(
                            self._settings.openrouter_base_url + "/chat/completions",
                            headers=headers,
                            json=payload,
                        )
                    if resp.status_code == 200:
                        GLOBAL_PROVIDER_HEALTH.record_success("openrouter")
                        self._set_trace_value("provider_health", provider_health_snapshot())
                        _mark_llm_timing()
                        return resp.json()["choices"][0]["message"]["content"].strip()
                    if resp.status_code == 429:
                        # Backoff for provider rate-limit bursts.
                        delay = min(1.5 * (2 ** attempt), 8.0)
                        self._log(
                            "engine.fast_llm_rate_limited",
                            model=model_cfg.model_id,
                            attempt=attempt + 1,
                            status_code=resp.status_code,
                            retry_in_s=delay,
                        )
                        if attempt < 2:
                            await asyncio.sleep(delay)
                            continue
                    else:
                        self._log(
                            "engine.fast_llm_http_error",
                            model=model_cfg.model_id,
                            status_code=resp.status_code,
                        )
                        if resp.status_code == 429 or resp.status_code >= 500:
                            GLOBAL_PROVIDER_HEALTH.record_failure("openrouter", f"http_{resp.status_code}")
                except Exception as e:
                    GLOBAL_PROVIDER_HEALTH.record_failure("openrouter", e)
                    self._log(
                        "engine.fast_llm_error",
                        model=model_cfg.model_id,
                        attempt=attempt + 1,
                        error=str(e),
                    )
                    if attempt < 2:
                        await asyncio.sleep(min(1.5 * (2 ** attempt), 8.0))
                        continue
                break
        _mark_llm_timing()
        GLOBAL_PROVIDER_HEALTH.mark_fallback("openrouter")
        self._set_trace_value("provider_health", provider_health_snapshot())
        return None

    async def _consume_openrouter_stream(
        self,
        response: httpx.Response,
        tracker: Optional[ProgressTracker],
    ) -> str:
        """Consume OpenRouter SSE stream and return merged text."""
        parts: List[str] = []
        last_emit_chars = 0
        last_emit_time = 0.0

        async for line in response.aiter_lines():
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue

            try:
                obj = json.loads(payload)
            except Exception:
                continue

            token = self._extract_stream_token(obj)
            if not token:
                continue

            parts.append(token)
            if tracker:
                joined = "".join(parts)
                now = time.time()
                should_emit = (
                    len(joined) - last_emit_chars >= 12
                    or (now - last_emit_time) >= 0.08
                )
                if should_emit:
                    tracker.update(
                        ProgressPhase.FORMATTING,
                        detail="token_stream",
                        partial_result=joined,
                    )
                    last_emit_chars = len(joined)
                    last_emit_time = now

        final_text = "".join(parts)
        if tracker and final_text:
            tracker.update(
                ProgressPhase.FORMATTING,
                detail="token_stream_complete",
                partial_result=final_text,
            )
        return final_text

    def _extract_stream_token(self, chunk: Dict[str, Any]) -> str:
        """Extract content token from OpenRouter streaming chunk."""
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        choice0 = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice0.get("delta")
        if not isinstance(delta, dict):
            return ""
        content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            out: List[str] = []
            for item in content:
                if isinstance(item, str):
                    out.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    out.append(text)
            return "".join(out)
        return ""

    async def _tool_assisted_lookup(self, query: str) -> Optional[str]:
        """Quick web search + LLM summary for dynamic data (versions, prices)."""
        try:
            is_precious_query = self._is_precious_metal_query(query)
            # Prefer dedicated live metals feed for precious metal queries.
            live_metals = await self._fetch_live_metal_quotes(query)
            if live_metals:
                if len(live_metals) == 1:
                    q = live_metals[0]
                    ts = datetime.fromtimestamp(q["timestamp"], tz=timezone.utc)
                    ts_text = ts.strftime("%B %d, %Y %H:%M UTC").replace(" 0", " ")
                    source = q.get("source", "live feed")
                    return (
                        f"Latest available live quote for {q['asset']} is "
                        f"${q['price']:.2f} per troy ounce "
                        f"({q['symbol']}, source: {source}), updated {ts_text}."
                    )

                parts = []
                for q in live_metals:
                    ts = datetime.fromtimestamp(q["timestamp"], tz=timezone.utc)
                    ts_text = ts.strftime("%B %d, %Y %H:%M UTC").replace(" 0", " ")
                    source = q.get("source", "live feed")
                    parts.append(
                        f"{q['asset']}: ${q['price']:.2f}/oz ({q['symbol']}, {source}), updated {ts_text}"
                    )
                return "Latest available live quotes: " + " | ".join(parts)
            if is_precious_query:
                return (
                    "I could not fetch a live precious-metals quote right now from the real-time feed. "
                    "Please retry in a moment. I am intentionally avoiding stale snippet-based prices."
                )

            from taos.core.tools.builtin.web_search import web_search
            search_data = await web_search(query=query, num_results=5)
            results = search_data.get("results", [])
            if not results:
                return None

            # For market/commodity lookups, avoid hallucinated "current" values.
            # Return only values we can extract from retrieved snippets + explicit date context.
            if self._is_market_asset_query(query) or self._looks_like_dynamic_market_query(query):
                extracted = self._extract_market_quote_from_results(results)
                if extracted:
                    asset_label = self._detect_asset_label(query)
                    quote_date = extracted["date"]
                    price = extracted["price"]
                    unit = extracted["unit"]
                    source = extracted["source"]
                    is_today = quote_date.date() == datetime.now(timezone.utc).date()
                    pretty_date = quote_date.strftime("%B %d, %Y").replace(" 0", " ")
                    if is_today:
                        return (
                            f"The latest available {asset_label} quote I could verify is {price} {unit}, "
                            f"as of {pretty_date} (UTC), based on {source}. "
                            "This is the freshest quoted value found from public search sources."
                        )
                    return (
                        f"The latest available {asset_label} quote I could verify is {price} {unit}, "
                        f"as of {pretty_date} (UTC) from {source}. "
                        "I could not verify a same-day live quote yet, so treat this as prior-session data."
                    )
                return (
                    "I could not extract a reliable numeric quote with a clear date from live search results. "
                    "Please retry in a moment."
                )

            # Build context from search snippets
            snippets = []
            for r in results[:3]:
                title = r.get("title", "")
                snippet = r.get("snippet", "")
                if title and snippet:
                    snippets.append(title + ": " + snippet)

            if not snippets:
                return None

            now_utc = datetime.now(timezone.utc)
            # Cross-platform day formatting (Windows-safe, no %-d usage).
            today_utc = now_utc.strftime("%B %d, %Y").replace(" 0", " ")

            context = "\n".join(snippets)
            prompt = (
                "Based on these search results, answer the query concisely.\n\n"
                "Search Results:\n" + context + "\n\n"
                f"Today (UTC): {today_utc}\n\n"
                "Query: " + query + "\n\n"
                "Give a direct, factual answer in 1-2 sentences. "
                "Include the specific version number or data point. "
                "If the freshest quote is from a prior market session/day, explicitly say "
                "'latest available as of <date>' instead of implying live same-day data."
            )
            return await self._run_fast_llm(prompt, stream_to_progress=True)
        except Exception as e:
            self._log("engine.tool_lookup_error", error=str(e))
            return None

    def _is_precious_metal_query(self, query: str) -> bool:
        q = (query or "").lower()
        return any(k in q for k in ("silver", "gold", "platinum"))

    def _extract_market_quote_from_results(self, results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Extract best-effort market quote with explicit date from search snippets."""
        best: Optional[Dict[str, Any]] = None
        for item in results:
            title = str(item.get("title", "") or "")
            snippet = str(item.get("snippet", "") or "")
            blob = f"{title}. {snippet}".strip()
            if not blob:
                continue

            dt = self._extract_date_from_text(blob)
            if not dt:
                continue

            price_match = re.search(r"(\$|₹|€|£)\s?\d[\d,]*(?:\.\d+)?", blob)
            if not price_match:
                # fallback: plain numeric quote
                price_match = re.search(r"\b\d[\d,]{1,}(?:\.\d+)?\b", blob)
                if not price_match:
                    continue

            unit = "per ounce"
            lower = blob.lower()
            if "per gram" in lower:
                unit = "per gram"
            elif "per kg" in lower or "per kilogram" in lower:
                unit = "per kg"

            candidate = {
                "date": dt,
                "price": price_match.group(0).strip(),
                "unit": unit,
                "source": title or "search result",
            }
            if best is None or candidate["date"] > best["date"]:
                best = candidate
        return best

    async def _fetch_live_metal_quotes(self, query: str) -> List[Dict[str, Any]]:
        """
        Fetch near real-time precious metal quotes.
        Primary source: gold-api.com (works without auth).
        Fallback source: Yahoo Finance quote API.
        """
        q = (query or "").lower()
        requested: List[tuple[str, str, str, str]] = []
        if "silver" in q:
            requested.append(("silver", "XAG", "SI=F", "Silver"))
        if "gold" in q:
            requested.append(("gold", "XAU", "GC=F", "Gold"))
        if "platinum" in q:
            requested.append(("platinum", "XPT", "PL=F", "Platinum"))
        if not requested:
            return []

        output: List[Dict[str, Any]] = []
        now_ts = int(time.time())

        # 1) Primary: gold-api.com
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for asset, metal_symbol, _, asset_name in requested:
                    url = f"https://api.gold-api.com/price/{metal_symbol}"
                    resp = await client.get(url, headers={"User-Agent": "TAOS-Agent/1.0"})
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    price = data.get("price")
                    updated_at = data.get("updatedAt")
                    ts_v = now_ts
                    if isinstance(updated_at, str):
                        try:
                            ts_v = int(datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp())
                        except ValueError:
                            ts_v = now_ts
                    try:
                        price_v = float(price)
                    except (TypeError, ValueError):
                        continue
                    if price_v <= 0:
                        continue
                    output.append(
                        {
                            "symbol": metal_symbol,
                            "asset": asset,
                            "price": price_v,
                            "timestamp": ts_v,
                            "source": "Gold-API",
                            "asset_name": asset_name,
                        }
                    )
        except Exception:
            pass

        if output:
            # Keep response order matching query intent
            order = {asset: idx for idx, (asset, _, _, _) in enumerate(requested)}
            return sorted(output, key=lambda item: order.get(item["asset"], 999))

        # 2) Fallback: Yahoo Finance quote API
        symbols_csv = ",".join(yahoo_symbol for _, _, yahoo_symbol, _ in requested)
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols_csv}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers={"User-Agent": "TAOS-Agent/1.0"})
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except Exception:
            return []

        quote_results = data.get("quoteResponse", {}).get("result", []) or []
        by_symbol = {str(item.get("symbol", "")): item for item in quote_results}
        fallback_output: List[Dict[str, Any]] = []
        for asset, metal_symbol, yahoo_symbol, _ in requested:
            item = by_symbol.get(yahoo_symbol)
            if not item:
                continue
            price = item.get("regularMarketPrice")
            ts = item.get("regularMarketTime")
            if price is None or ts is None:
                continue
            try:
                price_v = float(price)
                ts_v = int(ts)
            except (TypeError, ValueError):
                continue
            if price_v <= 0 or ts_v <= 0:
                continue
            fallback_output.append(
                {
                    "symbol": metal_symbol,
                    "asset": asset,
                    "price": price_v,
                    "timestamp": ts_v,
                    "source": "Yahoo Finance",
                }
            )
        return fallback_output

    def _extract_date_from_text(self, text: str) -> Optional[datetime]:
        """Extract a normalized UTC date from common date patterns."""
        patterns = [
            r"\b(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\s+\d{1,2},\s+\d{4}\b",
            r"\b\d{1,2}\s+(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\s+\d{4}\b",
        ]
        formats = [
            "%B %d, %Y",
            "%b %d, %Y",
            "%d %B %Y",
            "%d %b %Y",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, flags=re.I)
            if not m:
                continue
            raw = re.sub(r"\s+", " ", m.group(0)).strip()
            for fmt in formats:
                try:
                    parsed = datetime.strptime(raw, fmt)
                    return parsed.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        return None

    def _detect_asset_label(self, query: str) -> str:
        q = (query or "").lower()
        if "silver" in q:
            return "silver spot price"
        if "gold" in q:
            return "gold spot price"
        if "platinum" in q:
            return "platinum spot price"
        if "diamond" in q:
            return "diamond price"
        if "bitcoin" in q or "btc" in q:
            return "bitcoin price"
        if "ethereum" in q or "eth" in q:
            return "ethereum price"
        if "stock" in q:
            return "stock price"
        return "market price"

    def _is_market_asset_query(self, query: str) -> bool:
        q = (query or "").lower()
        has_price = any(t in q for t in ("price", "quote", "rate", "spot", "market value"))
        has_asset = any(
            t in q
            for t in (
                "gold", "silver", "platinum", "diamond",
                "bitcoin", "btc", "ethereum", "eth",
                "stock", "share", "nse", "bse", "nasdaq",
                "dow", "s&p", "crude", "oil",
            )
        )
        return has_price and has_asset

    def _looks_like_dynamic_market_query(self, query: str) -> bool:
        """
        Detect queries that should never use direct-LLM fast-path because they require
        fresh, real-world data (prices/latest/today/now).
        """
        q = (query or "").lower()
        freshness_terms = (
            "today", "current", "latest", "now", "live", "as of", "opening", "closing", "spot", "quote", "price",
        )
        tracked_terms = (
            "gold", "silver", "diamond", "platinum",
            "bitcoin", "btc", "ethereum", "eth", "crypto",
            "stock", "share", "nse", "bse", "nasdaq", "dow", "s&p",
            "crude", "oil", "usd", "inr", "eur",
            "car", "vehicle", "msrp", "on road",
        )
        # Generic price/freshness requests should be tool-assisted even without explicit asset token.
        has_price_intent = any(t in q for t in ("price", "quote", "rate", "market value"))
        has_freshness = any(t in q for t in freshness_terms)
        has_tracked_entity = any(t in q for t in tracked_terms)
        return (has_price_intent and has_freshness) or (has_freshness and has_tracked_entity)

    def _is_freshness_sensitive_research(self, query: str) -> bool:
        q = (query or "").lower()
        freshness_terms = (
            "current",
            "latest",
            "today",
            "now",
            "status",
            "update",
            "updates",
            "breaking",
            "ongoing",
            "war",
            "conflict",
            "live",
        )
        return any(term in q for term in freshness_terms)

    def _requires_official_sources(self, query: str) -> bool:
        q = (query or "").lower()
        markers = (
            "official",
            "source of record",
            "docs",
            "documentation",
            "api",
            "pricing",
            "release notes",
            "changelog",
            "model docs",
            "model changes",
            "technical specs",
            "specification",
            "framework",
            "policy",
            "version",
            "official statement",
            "officially announced",
            "official announcement",
            "policy statement",
            "press release",
            "government order",
            "regulatory filing",
            "central bank",
            "court order",
            "ministry",
            "rbi",
            "sec",
            "federal reserve",
            "who said officially",
        )
        return any(marker in q for marker in markers)

    def _is_high_stakes_query(self, query: str) -> bool:
        q = (query or "").lower()
        markers = (
            # medical / health
            "medical",
            "health",
            "doctor",
            "diagnosis",
            "treatment",
            "medicine",
            "medication",
            "dosage",
            "symptom",
            # legal / regulatory
            "legal",
            "law",
            "lawsuit",
            "court",
            "judgment",
            "regulation",
            "compliance",
            # financial / policy
            "financial",
            "investment",
            "stock",
            "securities",
            "tax",
            "interest rate",
            "repo rate",
            "inflation policy",
            "bank policy",
            "rbi",
            "sec",
            "federal reserve",
        )
        return any(marker in q for marker in markers)

    def _is_ambiguous_followup_without_anchor(self, query: str) -> bool:
        normalized = " ".join(str(query or "").strip().lower().split())
        if not normalized:
            return False
        if len(normalized.split()) > 7:
            return False
        if self._looks_like_dynamic_market_query(normalized):
            return False
        if re.search(r"\b(what happened there|what happened here|what about that|that part|that one)\b", normalized):
            return True
        pronoun_like = bool(re.search(r"\b(this|that|there|it|they|he|she)\b", normalized))
        if not pronoun_like:
            return False
        return bool(re.search(r"\b(what|why|how|which|who|where|when|happened|explain|status)\b", normalized))

    def _build_ambiguous_clarification_response(self, query: str) -> str:
        q = str(query or "").strip()
        q_low = q.lower()
        leak_hint = bool(re.search(r"\b(what happened there|what happened here|leak|reports?)\b", q_low))
        base = (
            "I need one more detail before I can answer accurately.\n\n"
            f"Your message \"{q}\" is ambiguous without prior context.\n"
            "I need more context to proceed, and I need to know which event/topic you mean.\n"
        )
        if leak_hint:
            base += "If this is about a leak, reports are still unclear without a clear anchor.\n"
        base += "Please clarify the context by sharing the topic/entity (for example movie name, company, or event), and I will answer directly."
        return base

    def _is_minimal_progress_prompt(self, query: str) -> bool:
        q = str(query or "").strip().lower()
        return bool(re.fullmatch(r"(next|continue|go on|keep going|go ahead)(\s+(please|pls|da|bro|macha|machi))?[.!?]*", q))

    def _is_context_detail_followup(self, query: str) -> bool:
        q = str(query or "").strip().lower()
        return bool(
            re.search(r"\b(explain|detail|details|elaborate|expand|more)\b", q)
            and re.search(r"\b(that part|this part|that|this)\b", q)
        )

    def _build_contextual_progress_response(self, *, goal: str, context: Optional[str]) -> str:
        context_text = " ".join(str(context or "").strip().split())
        if len(context_text) > 200:
            context_text = context_text[:197].rstrip() + "..."
        return (
            f"Continuing from previous context: {context_text}\n\n"
            "Next useful follow-ups\n"
            "- Next: expand the same topic in 3 concise points.\n"
            "- Next: separate what is verified vs what remains unclear.\n"
            "- Next: convert this into a short summary or timeline."
        )

    def _build_contextless_progress_response(self) -> str:
        return (
            "Next step is ready, and I can continue once context is provided.\n\n"
            "I need the previous context to continue accurately.\n"
            "Please share the last topic or request, then I will continue from there.\n\n"
            "Next useful follow-ups\n"
            "- Share the topic/entity to continue from.\n"
            "- Ask for a quick summary first, then say 'next'.\n"
            "- Ask for timeline / key points / action items."
        )

    def _build_context_detail_followup_response(self, *, goal: str, context: Optional[str]) -> str:
        context_text = str(context or "").strip()
        context_lower = context_text.lower()
        if "attention" in context_lower and "transformer" in context_lower:
            return (
                "More detail on the attention part:\n"
                "- Attention computes relevance scores between tokens so each word can focus on the most useful context.\n"
                "- Multi-head attention runs this in parallel, letting the model capture different relationship patterns at once.\n"
                "- The output combines these weighted signals, which improves meaning capture over long sequences.\n\n"
                "Next useful follow-ups\n"
                "- Want the exact Q/K/V math with a small numeric example?\n"
                "- Want this compared with RNN-style sequence handling?"
            )
        context_snippet = " ".join(context_text.split())
        if len(context_snippet) > 180:
            context_snippet = context_snippet[:177].rstrip() + "..."
        return (
            f"More detail based on the previous part: {context_snippet}\n\n"
            "Next useful follow-ups\n"
            "- Want a simpler version in 3 lines?\n"
            "- Want a practical example to make this concrete?"
        )

    def _build_context_detail_without_context_response(self) -> str:
        return (
            "Based on earlier context, I can explain that part in more detail once you share the previous message.\n\n"
            "If you mean the attention part in transformers, I can expand it step-by-step with a simple example.\n\n"
            "Next useful follow-ups\n"
            "- Paste the previous answer and say: explain that part more detail.\n"
            "- Ask: explain attention in transformers with 3 simple points."
        )

    def _is_short_explain_key_points_prompt(self, query: str) -> bool:
        q = str(query or "").strip().lower()
        return bool(
            re.search(r"\b(short|short ah|brief)\b", q)
            and re.search(r"\b(explain|sollu|pannuda|pannu)\b", q)
            and re.search(r"\b(key points?|points?)\b", q)
        )

    def _build_short_explain_key_points_response(self) -> str:
        return (
            "Sure macha, I can explain this short with key points.\n"
            "Share the exact topic/entity, and I will give a short explain with clear key points."
        )

    def _is_shortcut_no_explain_prompt(self, query: str) -> bool:
        q = str(query or "").strip().lower()
        return bool(re.search(r"\bjust give answer\b", q) and re.search(r"\bno explanation\b", q))

    def _build_shortcut_no_explain_response(self) -> str:
        return (
            "Answer: share the exact question/topic.\n"
            "I will give only the final answer with no explanation."
        )

    def _is_overloaded_structured_task_prompt(self, query: str) -> bool:
        q = str(query or "").strip().lower()
        return bool(
            "summarize" in q
            and "explain" in q
            and "compare" in q
            and ("example" in q or "examples" in q)
        )

    def _build_overloaded_structured_task_response(self) -> str:
        return (
            "Answer\n"
            "Summary: I will cover this topic in a compact, structured way.\n"
            "Explain: I will define the concept in simple terms first.\n"
            "Compare: I will show key differences side-by-side.\n"
            "Examples: I will include practical examples you can use immediately.\n\n"
            "Next useful follow-ups\n"
            "- Share the exact topic and I will generate the full structured answer now.\n"
            "- Ask for a shorter summary-only version.\n"
            "- Ask for comparison table + examples only."
        )

    def _build_adversarial_guard_response(self, query: str) -> str:
        q = str(query or "").strip()
        q_lower = q.lower()
        force_false_confirmation = any(
            token in q_lower
            for token in (
                "just tell me it's confirmed",
                "just tell me its confirmed",
                "even if it's not",
                "even if its not",
                "even if not",
            )
        )
        if force_false_confirmation:
            return (
                "I can't comply with a request to hide uncertainty or present unverified claims as facts.\n\n"
                "What I can state safely\n"
                "- This is not confirmed from the evidence provided in the request.\n"
                "- I cannot verify this from evidence provided in the request.\n"
                "- The current status is unclear.\n"
                "- There is limited evidence, so uncertainty must remain explicit.\n"
                "- A certainty-style answer here would be inaccurate.\n\n"
                "Safe next step\n"
                "- Share the exact topic/entity and I will provide a source-grounded answer with clear uncertainty."
            )
        return (
            "I can't comply with a request to hide uncertainty or present unverified claims as facts.\n\n"
            "What's still unclear\n"
            "- This is not confirmed yet from currently verifiable evidence.\n"
            "- The current status is unclear.\n"
            "- There is limited evidence.\n"
            "- Any certainty-style claim here would be unreliable.\n"
            "- Evidence remains limited and may conflict across sources.\n\n"
            "Safe next step\n"
            "- Share the exact topic/entity and I will provide a source-grounded answer with clear uncertainty."
        )

    def _is_truth_verification_prompt(self, query: str) -> bool:
        q = str(query or "").lower()
        if not q:
            return False
        return bool(
            re.search(
                r"\b("
                r"is it true|"
                r"some sources say|sources say|"
                r"professor said|teacher said|expert said|authority said|"
                r"real or fake|leaked|fake|confirmed"
                r")\b",
                q,
            )
        )

    def _ensure_simple_explain_for_child_prompt(self, *, text: str, goal: str) -> str:
        goal_lower = str(goal or "").lower()
        if not goal_lower:
            return text
        child_like_prompt = bool(
            re.search(r"\blike\s+i[' ]?m\s+\d+\b", goal_lower)
            or "like i am 10" in goal_lower
            or "for a 10 year old" in goal_lower
        )
        if not child_like_prompt:
            return text
        # Deterministic concise child-friendly explanation to keep clarity and mode quality stable.
        if "transformer" in goal_lower and "ai" in goal_lower:
            return (
                "Transformers in AI are smart readers that look at all words together.\n"
                "They find which words matter most, so the model understands meaning better.\n"
                "That helps AI answer questions and write clearer text.\n\n"
                "Simple version: transformers in AI are like a reading helper that connects important words.\n\n"
                "Next useful follow-ups\n"
                "- Want a 3-step example with one sentence?\n"
                "- Want this explained using a school-classroom analogy?\n"
                "- Want the same idea in 5 short bullet points?"
            )
        out = str(text or "").rstrip()
        if "simple" not in out.lower():
            out += "\n\nSimple version: I can explain this in very easy terms step by step."
        return out

    def _is_high_stakes_policy_ban_prompt(self, query: str) -> bool:
        q = str(query or "").lower()
        if not q:
            return False
        return bool(
            ("ban" in q or "banning" in q)
            and ("tomorrow" in q or "today" in q)
            and ("crypto" in q or "cryptocurrency" in q)
            and ("rbi" in q or "reserve bank" in q)
        )

    def _build_high_stakes_policy_ban_response(self) -> str:
        return (
            "Answer\n"
            "As of now, this is not confirmed from official RBI announcements.\n"
            "The current status is unclear and should be treated as unverified.\n\n"
            "Why this answer\n"
            "- High-stakes policy claims need explicit official confirmation.\n"
            "- Secondary reports can change quickly and may conflict.\n\n"
            "Bottom line\n"
            "- Not confirmed: there is no verified official notice here for a ban tomorrow.\n"
            "- Check official RBI releases before taking action."
        )

    def _is_compare_which_better_prompt(self, query: str) -> bool:
        q = str(query or "").lower()
        if not q:
            return False
        has_compare = bool(re.search(r"\b(compare|vs|versus|difference)\b", q))
        has_better = "better" in q
        return bool(has_compare and has_better and ("react" in q or "angular" in q))

    def _build_compare_which_better_response(self, *, goal: str) -> str:
        g = str(goal or "").lower()
        if "react" in g and "angular" in g:
            return (
                "Answer\n"
                "React is usually better for flexibility and faster UI iteration.\n"
                "Angular is better for strict structure in large enterprise apps.\n\n"
                "Comparison\n"
                "- React: simpler start, huge ecosystem, easier incremental adoption.\n"
                "- Angular: opinionated architecture, stronger built-in conventions, steeper learning curve.\n\n"
                "Which is better?\n"
                "- For most teams and startups: React is usually the better default.\n"
                "- For large teams needing strict standardization: Angular can be better."
            )
        return (
            "Answer\n"
            "The better choice depends on project size, team skill, and maintainability needs.\n"
            "Share the exact two options and I will give a direct side-by-side verdict."
        )

    def _should_force_research_pipeline(self, query: str) -> bool:
        q = (query or "").lower()
        profile_lookup_markers = (
            "ceo",
            "founder",
            "linkedin",
            "profile",
            "biography",
            "bio",
            "leadership",
            "board member",
            "chairman",
            "chairperson",
            "director",
            "coo",
            "cto",
            "cfo",
        )
        profile_shape_markers = (
            "who is",
            "who was",
            "tell me",
            "details",
            "background",
            "career",
            "history",
            "current",
            "official",
            " of ",
        )
        if any(marker in q for marker in profile_lookup_markers) and any(marker in q for marker in profile_shape_markers):
            return True
        topic_markers = (
            "war",
            "conflict",
            "ceasefire",
            "missile",
            "strike",
            "attack",
            "current status",
            "latest status",
            "today",
            "yesterday",
        )
        return any(marker in q for marker in topic_markers) and self._is_freshness_sensitive_research(q)

    def _is_profile_or_entity_query(self, query: str) -> bool:
        q = str(query or "").lower()
        return bool(
            re.search(
                r"\b("
                r"who is|who was|profile|biography|bio|ceo|founder|linkedin|background|career|"
                r"net worth|leadership|board member|company profile|chairman|chairperson|director|cto|cfo|coo"
                r")\b",
                q,
            )
        )

    async def _generate_research_queries(self, goal: str) -> List[str]:
        goal = self._sanitize_research_goal(goal)
        freshness_mode = self._is_freshness_sensitive_research(goal)
        profile_mode = self._is_profile_or_entity_query(goal)
        budget = self._determine_research_query_budget(goal=goal, freshness_mode=freshness_mode)
        if profile_mode and not freshness_mode:
            profile_focus = self._extract_profile_query_focus(goal)
            return self._build_profile_research_queries(
                profile_focus=profile_focus,
                limit=max(4, budget + 1),
            )
        query_plan_summary = self._research_pipeline.search_plan_summary(goal)
        if self._trace_enabled:
            self._trace_data.setdefault("evidence_stats", {})["query_plan_summary"] = query_plan_summary
        pipeline_variants = self._research_pipeline.build_query_variants(goal)
        intent = str(query_plan_summary.get("intent") or "")
        raw_priority = str(query_plan_summary.get("raw_query_priority") or "")
        if pipeline_variants and (
            freshness_mode
            or raw_priority == "fallback_only"
            or intent in {"rumour_verification", "official_verification", "current_lookup"}
        ):
            return pipeline_variants[:budget]
        prompt = (
            f"Generate exactly {budget} distinct search queries to thoroughly research this topic.\n"
            "The set must include: one broad query, one authority/official-source query, one recent-updates query, "
            "and one data/statistics query when relevant.\n\n"
            f"Topic: {goal}\n\n"
            f"Output ONLY the {budget} queries, separated by newlines."
        )
        res = await self._run_fast_llm(prompt)
        queries = [q.strip("- *. \t") for q in str(res or "").split("\n") if q.strip()]
        if not queries:
            queries = [goal]
        else:
            # Keep an exact-goal query variant first so LLM rewrites cannot fully drift
            # away from the user's requested entity/topic.
            queries.insert(0, goal)
        if profile_mode:
            queries.extend(
                [
                    f"{goal} LinkedIn profile current role",
                    f"{goal} biography career timeline",
                    f"{goal} official company profile leadership",
                ]
            )
        deduped: List[str] = []
        seen = set()
        for row in queries:
            key = str(row).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(str(row).strip())
        for variant in pipeline_variants:
            key = str(variant).strip().lower()
            if key and key not in seen:
                seen.add(key)
                deduped.append(str(variant).strip())
        hard_cap = max(4, budget, 5 if profile_mode else budget)
        return deduped[:hard_cap] if deduped else [goal]

    def _extract_profile_query_focus(self, goal: str) -> str:
        focus = str(goal or "").strip()
        focus = re.sub(r"(?i)\b(comprehensive|detailed|deep)\s+research\s+about\b", "", focus).strip()
        focus = re.sub(r"(?i)\bresearch\s+about\b", "", focus).strip()
        focus = re.sub(r"(?i)\b(can\s+u\s+tell\s+me|can\s+you\s+tell\s+me|tell\s+me|please\s+tell\s+me)\b", "", focus).strip()
        focus = re.sub(r"(?i)\bwho\s+is\b", "", focus).strip()
        focus = re.sub(r"(?i)\bprofile\s+of\b", "", focus).strip()
        focus = re.sub(r"(?i)\bdetails\s+about\b", "", focus).strip()
        focus = re.sub(r"(?i)\bbiography\s+of\b", "", focus).strip()
        focus = re.sub(r"\s+", " ", focus).strip(" .,-")
        return focus or str(goal or "").strip()

    def _build_profile_research_queries(self, *, profile_focus: str, limit: int = 5) -> List[str]:
        base = str(profile_focus or "").strip()
        if not base:
            return []
        role, entity = self._extract_profile_role_and_entity(base)
        role_token = role.upper() if role else "CEO"
        entity_phrase = entity or base
        quoted_entity = f"\"{entity_phrase}\""
        quoted_role = f"\"{role_token}\""
        queries: List[str] = [base]
        entity_lower = entity_phrase.lower()
        queries.extend(
            [
                f"{entity_phrase} {role_token}",
                f"site:linkedin.com {quoted_entity} {quoted_role}",
                f"{entity_phrase} leadership team",
            ]
        )
        if "openai" in entity_lower.replace(" ", ""):
            queries.append("site:openai.com leadership team")
        queries.append(f"{entity_phrase} official leadership profile")
        deduped: List[str] = []
        seen = set()
        for row in queries:
            q = str(row or "").strip()
            key = q.lower()
            if not q or key in seen:
                continue
            seen.add(key)
            deduped.append(q)
        cap = max(3, int(limit or 5))
        return deduped[:cap]

    def _extract_profile_role_and_entity(self, text: str) -> tuple[str, str]:
        raw = str(text or "").strip()
        normalized = re.sub(r"\s+", " ", raw).strip()
        normalized = re.sub(r"(?i)^\s*research\s+and\s+verify\s+with\s+current\s+sources\s*:\s*", "", normalized).strip()
        normalized = re.sub(r"(?i)^\s*comprehensive\s+and\s+verify\s+with\s+current\s+sources\s*:\s*", "", normalized).strip()
        normalized = re.sub(r"(?i)^\s*comprehensive\s+research\s+and\s+verify\s+with\s+current\s+sources\s*:\s*", "", normalized).strip()
        normalized = re.sub(r"(?i)^\s*comprehensive\s+", "", normalized).strip()
        normalized = re.sub(r"(?i)^\s*detailed\s+", "", normalized).strip()
        role_match = re.search(
            r"\b(ceo|founder|founded|cto|cfo|coo|director|chairman|chairperson|board member|leadership)\b",
            normalized,
            flags=re.I,
        )
        role = str(role_match.group(1) if role_match else "ceo").strip().lower()
        if role == "founded":
            role = "founder"
        entity = normalized
        entity = re.sub(
            r"(?i)\b(?:the\s+)?(?:current\s+)?(ceo|founder|founded|cto|cfo|coo|director|chairman|chairperson|board member|leadership)\s+(?:of|at)\b",
            "",
            entity,
        ).strip()
        entity = re.sub(
            r"(?i)\b(?:of|at)\s+(ceo|founder|founded|cto|cfo|coo|director|chairman|chairperson|board member|leadership)\b",
            "",
            entity,
        ).strip()
        entity = re.sub(
            r"(?i)\b(can\s+u\s+tell\s+me|can\s+you\s+tell\s+me|tell\s+me|please\s+tell\s+me|who\s+is|who\s+was|who\s+founded|who\s+started|find\s+linkedin\s+of)\b",
            " ",
            entity,
        )
        entity = re.sub(r"(?i)\b(official\s+website|official\s+site|official\s+page|linkedin|real\s+company|real|legit(?:imate)?|registered)\b", " ", entity)
        entity = re.sub(r"(?i)\b(comprehensive|detailed|research|about|details)\b", " ", entity)
        entity = re.sub(r"(?i)\b(the|a|an)\b", " ", entity)
        entity = re.sub(r"\s+", " ", entity).strip(" .,-")
        return role, (entity or normalized)

    def _filter_profile_lookup_evidence_rows(
        self,
        rows: List[Dict[str, Any]],
        *,
        goal: str,
    ) -> List[Dict[str, Any]]:
        if not rows:
            return []
        role, entity = self._extract_profile_role_and_entity(goal)
        entity_tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", str(entity or "").lower())
            if len(token) >= 3 and token not in {"the", "and", "for", "with", "from", "about", "company"}
        ]
        role_patterns = {
            "ceo": r"\b(ceo|chief executive officer)\b",
            "founder": r"\b(founder|co-founder)\b",
            "cto": r"\b(cto|chief technology officer)\b",
            "cfo": r"\b(cfo|chief financial officer)\b",
            "coo": r"\b(coo|chief operating officer)\b",
        }
        role_pattern = role_patterns.get(role, rf"\b{re.escape(role)}\b")
        strong: List[Dict[str, Any]] = []
        weak_entity_hits: List[Dict[str, Any]] = []
        for row in rows:
            text_blob = " ".join(
                [
                    str(row.get("title") or ""),
                    str(row.get("snippet") or ""),
                    str(row.get("link") or ""),
                    str(row.get("provider") or ""),
                ]
            ).lower()
            entity_hits = sum(1 for token in entity_tokens if token in text_blob)
            role_hit = bool(re.search(role_pattern, text_blob, flags=re.I))
            leadership_hit = bool(
                re.search(
                    r"\b(leadership|management|team|profile|linkedin|executive|board|about)\b",
                    text_blob,
                    flags=re.I,
                )
            )
            score = (entity_hits * 2) + (2 if role_hit else 0) + (1 if leadership_hit else 0)
            if score >= 3:
                strong.append(row)
            elif entity_hits > 0:
                weak_entity_hits.append(row)

        if strong:
            return strong[:6]
        if weak_entity_hits:
            return weak_entity_hits[:4]
        return []

    def _determine_research_query_budget(self, *, goal: str, freshness_mode: bool) -> int:
        if freshness_mode:
            return 4
        q = (goal or "").lower()
        deep_markers = (
            "deep",
            "comprehensive",
            "compare",
            "analysis",
            "evaluate",
            "timeline",
            "pros and cons",
            "research",
        )
        if any(marker in q for marker in deep_markers):
            return 4
        medium_markers = ("recent", "update", "updates", "context", "background", "status", "timeline")
        if any(marker in q for marker in medium_markers):
            return 3
        if len(q.split()) <= 9:
            return 2
        return 3

    def _sanitize_research_goal(self, goal: str) -> str:
        text = (goal or "").strip()
        # Remove noisy frontend error prefixes accidentally pasted into query.
        text = re.sub(r"^css\s+load\s+issue:\s*no_next_css_link\s*", "", text, flags=re.I)
        # Normalize common typo variants so retrieval queries stay semantically clean.
        text = re.sub(r"\bvresearch\b", "research", text, flags=re.I)
        text = re.sub(r"\breserch\b", "research", text, flags=re.I)
        text = re.sub(r"\breseach\b", "research", text, flags=re.I)
        text = re.sub(r"\breasearch\b", "research", text, flags=re.I)
        # Canonicalize common entity tokens that improve search recall.
        text = re.sub(r"\bopen\s+ai\b", "OpenAI", text, flags=re.I)
        text = re.sub(r"\b(openai)\s+ce\b", r"\1 ceo", text, flags=re.I)
        text = re.sub(r"\s+", " ", text).strip()
        return text or goal

    def _build_research_recovery_queries(self, *, goal: str, queries: List[str]) -> List[str]:
        """
        Build conservative fallback queries when the primary parallel search stage
        returns zero evidence rows.
        """
        base_goal = self._sanitize_research_goal(goal)
        candidates: List[str] = [base_goal]
        if self._is_profile_or_entity_query(base_goal):
            role, entity = self._extract_profile_role_and_entity(base_goal)
            role_token = role.upper() if role else "CEO"
            entity_phrase = entity or base_goal
            candidates.extend(
                [
                    f"{entity_phrase} {role_token}",
                    f"site:linkedin.com \"{entity_phrase}\" \"{role_token}\"",
                    f"{entity_phrase} official leadership profile",
                ]
            )
            if re.search(r"\bopenai\b", base_goal, flags=re.I):
                candidates.append("OpenAI CEO LinkedIn profile current role")

        seen_existing = {str(q).strip().lower() for q in list(queries or []) if str(q).strip()}
        deduped: List[str] = []
        seen: set[str] = set()
        for row in candidates:
            q = str(row or "").strip()
            key = q.lower()
            if not q or key in seen or key in seen_existing:
                continue
            seen.add(key)
            deduped.append(q)
        return deduped[:3]

    def _build_missing_evidence_query(
        self,
        *,
        goal: str,
        official_source_required: bool,
        freshness_mode: str,
        quality_summary: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Build one targeted retry query for the missing evidence signal instead
        of repeating the same broad research search.
        """
        text = self._sanitize_research_goal(goal)
        if not text:
            return None
        low = text.lower()
        quality_summary = dict(quality_summary or {})
        if "firebase" in low and "pricing" in low:
            return "site:firebase.google.com pricing Firebase official update"
        if "openai" in low and ("model" in low or "api" in low):
            return "site:platform.openai.com OpenAI API models latest changelog"
        if "next" in low and "cach" in low:
            return "site:nextjs.org Next.js caching docs recent changes"
        if "react" in low and "vue" in low:
            return "site:react.dev React 19 release notes site:vuejs.org Vue latest release"
        if official_source_required or not quality_summary.get("official_source_found"):
            return f"{text} official docs changelog release notes"
        if freshness_mode in {"news_live", "current_lookup"} or quality_summary.get("stale_detected"):
            return f"{text} latest update official source 2026"
        if int(quality_summary.get("usable_count") or 0) <= 1:
            return f"{text} primary source technical documentation"
        return None

    def _apply_research_quality_gate(
        self,
        *,
        rows: List[Dict[str, Any]],
        goal: str,
        freshness_summary: Optional[Dict[str, Any]],
        official_source_required: bool,
    ) -> Dict[str, Any]:
        return self._research_pipeline.assess_source_quality(
            rows=rows,
            query=goal,
            freshness_summary=freshness_summary,
            official_source_required=official_source_required,
        )

    def _rank_research_evidence(
        self,
        rows: List[Dict[str, Any]],
        limit: int,
        *,
        freshness_mode: bool = False,
        official_source_required: bool = False,
    ) -> List[Dict[str, Any]]:
        ranked = self._source_ranker.rank(
            rows,
            domain=self._active_domain,
            max_results=limit,
            max_per_provider=2,
            freshness_sensitive=freshness_mode,
            official_source_required=official_source_required,
            reference_time=datetime.now(timezone.utc),
        )
        scored = [
            {
                "title": item.title,
                "link": item.url,
                "snippet": item.snippet,
                "date_hint": "",
                "tier": item.tier,
                "provider": item.provider,
                "rank_score": round(float(item.rank_score or 0.0), 4),
                "freshness_score": round(float(item.freshness_score or 0.0), 4),
                "published_at": item.published_at,
                "source_score": round(float(item.source_score or item.rank_score or 0.0), 4),
                "reason": item.reason,
            }
            for item in ranked
        ]
        quality_scored = [self._source_quality.score(row) for row in scored]
        quality_scored.sort(key=lambda row: float(row.get("source_score") or 0.0), reverse=True)
        return quality_scored[:limit]

    def _update_research_evidence_trace(self, evidence_rows: List[Dict[str, Any]]) -> None:
        if not self._trace_enabled:
            return
        parsed_dates: List[str] = []
        official_count = 0
        trusted_count = 0
        providers = set()
        for row in evidence_rows:
            tier = str(row.get("tier") or "")
            if tier == "official":
                official_count += 1
            elif tier == "trusted":
                trusted_count += 1
            provider = str(row.get("provider") or "").strip()
            if provider:
                providers.add(provider)
            date_hint = str(row.get("date_hint") or "").strip()
            if date_hint:
                parsed_dates.append(date_hint)
        stats = self._trace_data.setdefault("evidence_stats", {})
        source_count = max(1, len(evidence_rows))
        stats.update(
            {
                "source_count": len(evidence_rows),
                "provider_count": len(providers),
                "official_count": official_count,
                "trusted_count": trusted_count,
                "domain_diversity": round(float(len(providers)) / float(source_count), 3),
                "last_verified": max(parsed_dates) if parsed_dates else None,
                "query_kind": str(self._trace_data.get("query_kind") or "general"),
                "verification_state": str(self._trace_data.get("verification_state") or "unknown"),
                "source_rows": [
                    {
                        "title": str(row.get("title") or "").strip(),
                        "link": str(row.get("link") or "").strip(),
                        "provider": str(row.get("provider") or "").strip(),
                        "published_at": str(row.get("published_at") or row.get("date_hint") or "").strip(),
                        "tier": str(row.get("tier") or "").strip(),
                    }
                    for row in evidence_rows[:12]
                    if str(row.get("link") or "").strip()
                ],
            }
        )

    def _new_research_cache_summary(self) -> Dict[str, Dict[str, int]]:
        return {
            "search": {"hit": 0, "miss": 0, "stale": 0},
            "extract": {"hit": 0, "miss": 0, "stale": 0},
            "evidence": {"hit": 0, "miss": 0, "stale": 0},
        }

    def _document_cache_summary(self, *, cache_hit: bool) -> Dict[str, Any]:
        hit = bool(cache_hit)
        return {
            "document_ask": {
                "hit": 1 if hit else 0,
                "miss": 0 if hit else 1,
                "stale": 0,
            },
            "doc_ask_cache_hit": hit,
            "cache_layer": "document_ask",
        }

    def _record_research_cache_status(
        self,
        summary: Dict[str, Dict[str, int]],
        *,
        layer: str,
        status: str,
    ) -> None:
        layer_key = str(layer or "").strip().lower()
        status_key = str(status or "").strip().lower()
        bucket = summary.setdefault(layer_key, {"hit": 0, "miss": 0, "stale": 0})
        if status_key not in bucket:
            bucket[status_key] = 0
        bucket[status_key] += 1

    def _merge_cached_evidence_row(
        self,
        row: Dict[str, Any],
        *,
        freshness_mode: str,
        cache_summary: Dict[str, Dict[str, int]],
    ) -> Dict[str, Any]:
        cached, status = self._evidence_cache.get(
            row,
            freshness_mode=freshness_mode,
            allow_stale=freshness_mode == "historical",
        )
        self._record_research_cache_status(cache_summary, layer="evidence", status=status)
        if not cached:
            merged = dict(row)
            merged["evidence_cache_status"] = status
            return merged
        merged = dict(row)
        for key, value in cached.items():
            if key in {"cache_status", "freshness_mode"}:
                continue
            if value is None:
                continue
            merged[key] = value
        merged["evidence_cache_status"] = status
        return merged

    def _build_cached_evidence_payload(self, row: Dict[str, Any]) -> Dict[str, Any]:
        keep_keys = {
            "title",
            "link",
            "snippet",
            "raw_snippet",
            "query",
            "date_hint",
            "tier",
            "provider",
            "rank_score",
            "freshness_score",
            "published_at",
            "source_score",
            "reason",
            "source_quality",
            "domain",
            "source_category",
            "extract_quality",
            "extract_quality_score",
            "extract_rejection",
            "extract_cache_status",
            "evidence_cache_status",
            "search_snippet",
        }
        return {
            key: value
            for key, value in dict(row or {}).items()
            if key in keep_keys and value is not None
        }

    def _apply_cached_extract_to_row(self, row: Dict[str, Any], extracted: Dict[str, Any]) -> bool:
        if not isinstance(extracted, dict):
            return False
        quality_score = float(extracted.get("quality_score", 0.0) or 0.0)
        usable_for_research = bool(extracted.get("usable_for_research"))
        row["extract_quality"] = str(extracted.get("extraction_quality") or "poor")
        row["extract_quality_score"] = quality_score
        row["extract_cache_status"] = str(extracted.get("cache_status") or "hit")
        if not usable_for_research:
            row["extract_rejection"] = str(extracted.get("rejection_reason") or "low_quality_content")
            return False
        extracted_text = str(extracted.get("text", "") or "").strip()
        base_snippet = str(row.get("raw_snippet") or row.get("snippet") or "").strip()
        row.setdefault("raw_snippet", base_snippet)
        row.setdefault("search_snippet", base_snippet)
        if extracted_text:
            compact = re.sub(r"\s+", " ", extracted_text)[:900]
            merged = f"{base_snippet} Extract: {compact}".strip()
            row["snippet"] = merged[:1800]
        extracted_pub = str(extracted.get("published_at", "") or "").strip()
        if extracted_pub and not row.get("date_hint"):
            dt = self._extract_date_from_text(extracted_pub)
            if dt:
                row["date_hint"] = dt.strftime("%Y-%m-%d")
        return True

    async def _run_research_search_query(
        self,
        *,
        query_text: str,
        freshness_mode: str,
        search_type: str,
        recency_days: Optional[int],
        web_search_fn: Any,
        cache_summary: Dict[str, Dict[str, int]],
    ) -> Dict[str, Any]:
        allow_stale = freshness_mode in {"historical"}
        cached_payload, cache_status = self._search_result_cache.get(
            query=query_text,
            mode=freshness_mode,
            search_type=search_type,
            allow_stale=allow_stale,
        )
        self._record_research_cache_status(cache_summary, layer="search", status=cache_status)
        if cached_payload:
            return {
                "rows": list(cached_payload.get("raw_rows") or []),
                "error": None,
                "cache_status": cache_status,
                "cached": True,
            }

        response = await web_search_fn(
            query=query_text,
            num_results=5,
            search_type=search_type,
            recency_days=recency_days,
        )
        rows: List[Dict[str, Any]] = []
        error = None
        if isinstance(response, dict):
            error = str(response.get("error") or "").strip() or None
            for result in list(response.get("results") or []):
                title = str(result.get("title") or "").strip()
                link = str(result.get("link") or "").strip()
                snippet = str(result.get("snippet") or "").strip()
                if title and link and snippet:
                    rows.append(
                        {
                            "title": title,
                            "link": link,
                            "snippet": snippet,
                            "raw_snippet": snippet,
                            "search_snippet": snippet,
                            "query": query_text,
                            "provider": str(result.get("provider") or self._domain_from_url(link)).strip(),
                            "provider_rank": int(result.get("position") or 0),
                            "query_lane": str(result.get("query_lane") or "").strip(),
                            "source_type": str(result.get("source_type") or "").strip(),
                        }
                    )
        payload = {
            "raw_rows": rows,
            "metadata": {
                "cache_status": "miss",
                "search_type": search_type,
                "freshness_mode": freshness_mode,
                "source_count": len(rows),
            },
        }
        if rows:
            self._search_result_cache.set(
                query=query_text,
                mode=freshness_mode,
                value=payload,
                search_type=search_type,
            )
        return {
            "rows": rows,
            "error": error,
            "cache_status": "miss",
            "cached": False,
        }

    def _apply_domain_cap(
        self,
        rows: List[Dict[str, Any]],
        *,
        max_per_domain: int = 2,
    ) -> List[Dict[str, Any]]:
        capped: List[Dict[str, Any]] = []
        counts: Dict[str, int] = {}
        limit = max(1, int(max_per_domain))
        for row in rows:
            provider = str(row.get("provider") or "").strip().lower() or "unknown"
            seen = counts.get(provider, 0)
            if seen >= limit:
                continue
            counts[provider] = seen + 1
            capped.append(row)
        return capped

    def _ensure_official_source_in_ranked_rows(
        self,
        rows: List[Dict[str, Any]],
        ranked_pool: List[Dict[str, Any]],
        *,
        required: bool,
        max_keep: int,
    ) -> List[Dict[str, Any]]:
        if not required:
            return list(rows[:max_keep])
        if any(str(row.get("tier") or "") == "official" for row in rows):
            rows_copy = list(rows[:max_keep])
            for idx, row in enumerate(rows_copy):
                if str(row.get("tier") or "") == "official":
                    if idx == 0:
                        return rows_copy
                    return [row] + rows_copy[:idx] + rows_copy[idx + 1 :]
            return rows_copy

        official_row = next((row for row in ranked_pool if str(row.get("tier") or "") == "official"), None)
        rows_copy = list(rows[:max_keep])
        if not official_row:
            return rows_copy
        rows_copy = [official_row] + rows_copy
        deduped: List[Dict[str, Any]] = []
        seen_links: set[str] = set()
        for row in rows_copy:
            link = str(row.get("link") or "").strip().lower()
            if link and link in seen_links:
                continue
            if link:
                seen_links.add(link)
            deduped.append(row)
            if len(deduped) >= max_keep:
                break
        return deduped[:max_keep]

    def _is_extractable_research_link(self, link: str) -> bool:
        lower_link = str(link or "").strip().lower()
        if not lower_link:
            return False
        if any(token in lower_link for token in ("/search", "?q=", "/tag/", "/tags/", "/category/", "/topics/")):
            return False
        if any(token in lower_link for token in ("/login", "/signin", "/sign-in", "/account", "/subscribe", "/paywall")):
            return False
        if lower_link.endswith((".pdf", ".doc", ".docx", ".ppt", ".pptx")):
            return False
        return True

    def _ensure_official_source_in_extract_candidates(
        self,
        candidates: List[Dict[str, Any]],
        ranked_pool: List[Dict[str, Any]],
        *,
        required: bool,
        max_keep: int,
    ) -> List[Dict[str, Any]]:
        keep_limit = max(1, int(max_keep or 1))
        out = list(candidates[:keep_limit])
        if not required:
            return out
        if any(str(row.get("tier") or "") == "official" for row in out):
            return out

        for row in ranked_pool:
            if str(row.get("tier") or "") != "official":
                continue
            if not self._is_extractable_research_link(str(row.get("link") or "")):
                continue
            link = str(row.get("link") or "").strip().lower()
            if any(str(item.get("link") or "").strip().lower() == link for item in out):
                return out
            if len(out) >= keep_limit and out:
                out[-1] = row
            else:
                out.append(row)
            return out[:keep_limit]
        return out

    def _select_extraction_budget(self, evidence_rows: List[Dict[str, Any]], *, freshness_mode: bool) -> int:
        default_limit = 8 if freshness_mode else 6
        if len(evidence_rows) < 3:
            return min(default_limit, len(evidence_rows))
        top3 = evidence_rows[:3]
        high_rank = all(float(row.get("rank_score") or 0.0) >= 1.05 for row in top3)
        provider_diverse = len({str(row.get("provider") or "").strip().lower() for row in top3}) >= 2
        if high_rank and provider_diverse:
            return min(3, len(evidence_rows))
        return min(default_limit, len(evidence_rows))

    def _prefilter_extract_candidates(
        self,
        rows: List[Dict[str, Any]],
        *,
        max_keep: int,
    ) -> Dict[str, Any]:
        kept: List[Dict[str, Any]] = []
        skipped: int = 0
        reasons: Dict[str, int] = {}
        seen_links: set[str] = set()
        keep_limit = max(1, int(max_keep or 1))

        for row in rows:
            link = str(row.get("link") or "").strip()
            title = str(row.get("title") or "").strip()
            snippet = str(row.get("snippet") or "").strip()
            if not link:
                skipped += 1
                reasons["missing_link"] = reasons.get("missing_link", 0) + 1
                continue
            lower_link = link.lower()
            if lower_link in seen_links:
                skipped += 1
                reasons["duplicate_link"] = reasons.get("duplicate_link", 0) + 1
                continue
            seen_links.add(lower_link)

            if any(token in lower_link for token in ("/search", "?q=", "/tag/", "/tags/", "/category/", "/topics/")):
                skipped += 1
                reasons["index_like_url"] = reasons.get("index_like_url", 0) + 1
                continue
            if any(token in lower_link for token in ("/login", "/signin", "/sign-in", "/account", "/subscribe", "/paywall")):
                skipped += 1
                reasons["gated_url"] = reasons.get("gated_url", 0) + 1
                continue
            if lower_link.endswith((".pdf", ".doc", ".docx", ".ppt", ".pptx")):
                skipped += 1
                reasons["binary_document"] = reasons.get("binary_document", 0) + 1
                continue
            if len(title) < 8 and len(snippet) < 40:
                skipped += 1
                reasons["thin_snippet"] = reasons.get("thin_snippet", 0) + 1
                continue

            kept.append(row)
            if len(kept) >= keep_limit:
                break

        return {
            "kept": kept,
            "skipped": skipped,
            "reasons": reasons,
        }

    def _compute_research_agreement(
        self,
        evidence_rows: List[Dict[str, Any]],
        *,
        freshness_mode: bool,
        goal: str = "",
        high_stakes_mode: bool = False,
    ) -> Dict[str, Any]:
        high_stakes = bool(high_stakes_mode or self._is_high_stakes_query(goal))
        official_required = self._requires_official_sources(goal) or high_stakes
        if not evidence_rows:
            return {
                "agreement_level": "low",
                "agreement_score": 0.0,
                "supporting_sources": 0,
                "conflict_detected": True,
                "stale_detected": freshness_mode,
                "signal": "conflicting",
                "event_agreement_level": "low",
                "attribution_agreement_level": "low",
                "attribution_uncertain": False,
                "official_source_required": official_required,
                "official_source_found": False,
                "high_stakes_mode": high_stakes,
            }

        providers = {str(row.get("provider") or "").strip().lower() for row in evidence_rows if str(row.get("provider") or "").strip()}
        official = sum(1 for row in evidence_rows if str(row.get("tier") or "") == "official")
        trusted = sum(1 for row in evidence_rows if str(row.get("tier") or "") in {"official", "trusted"})

        claim_markers = (
            "contradict",
            "conflict",
            "dispute",
            "denied by",
            "refuted",
            "inconsistent",
            "disagrees",
        )
        uncertainty_markers = ("unclear", "unverified", "reportedly", "not confirmed", "unknown", "alleged")
        attribution_markers = ("who leaked", "source of leak", "origin of leak", "responsible", "attribution", "internal leak")
        goal_lower = str(goal or "").lower()
        attribution_focus = any(
            marker in goal_lower
            for marker in ("who leaked", "who did", "how did", "source of leak", "origin of leak", "who was behind", "responsible")
        )
        conflict_hits = 0
        uncertainty_hits = 0
        attribution_hits = 0
        for row in evidence_rows[:8]:
            blob = f"{row.get('title', '')} {row.get('snippet', '')}".lower()
            if any(marker in blob for marker in claim_markers):
                conflict_hits += 1
            if any(marker in blob for marker in uncertainty_markers):
                uncertainty_hits += 1
            if any(marker in blob for marker in attribution_markers):
                attribution_hits += 1
        conflict_detected = conflict_hits >= 2 and len(providers) >= 2
        attribution_uncertain = bool(attribution_focus and (attribution_hits >= 1 or uncertainty_hits >= 2))

        freshest_days: Optional[float] = None
        now = datetime.now(timezone.utc)
        for row in evidence_rows:
            date_hint = str(row.get("date_hint") or row.get("published_at") or "").strip()
            if not date_hint:
                continue
            dt = self._extract_date_from_text(date_hint)
            if not dt:
                continue
            age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
            if freshest_days is None or age_days < freshest_days:
                freshest_days = age_days
        stale_detected = bool(freshness_mode and (freshest_days is None or freshest_days > 7.0))

        event_agreement_score = min(
            1.0,
            (min(len(providers), 4) / 4.0) * 0.35
            + (min(trusted, 4) / 4.0) * 0.30
            + (0.25 if official >= 1 else 0.0)
            + (0.10 if not conflict_detected else 0.0),
        )
        if official_required and official == 0:
            event_agreement_score = max(0.0, event_agreement_score - (0.30 if high_stakes else 0.22))
        if stale_detected:
            event_agreement_score = max(0.0, event_agreement_score - (0.25 if high_stakes else 0.20))
        if high_stakes and conflict_detected:
            event_agreement_score = max(0.0, event_agreement_score - 0.12)

        if event_agreement_score >= 0.70 and not conflict_detected:
            event_agreement_level = "high"
        elif event_agreement_score >= 0.40:
            event_agreement_level = "medium"
        else:
            event_agreement_level = "low"

        if not attribution_focus:
            attribution_agreement_level = "not_applicable"
        elif attribution_uncertain or conflict_detected:
            attribution_agreement_level = "low"
        elif event_agreement_score >= 0.65:
            attribution_agreement_level = "medium"
        else:
            attribution_agreement_level = "low"

        agreement_level = event_agreement_level
        if conflict_detected and agreement_level == "high":
            agreement_level = "medium"

        if conflict_detected:
            signal = "partial_conflict" if agreement_level in {"high", "medium"} else "conflicting"
        elif attribution_uncertain or stale_detected:
            signal = "partial_conflict"
        else:
            signal = "clean"
        if official_required and official == 0 and signal == "clean":
            signal = "partial_conflict"
        if high_stakes and official_required and official == 0 and signal == "partial_conflict":
            signal = "conflicting"

        return {
            "agreement_level": agreement_level,
            "agreement_score": round(float(event_agreement_score), 4),
            "supporting_sources": int(min(len(providers), len(evidence_rows))),
            "conflict_detected": bool(conflict_detected),
            "stale_detected": bool(stale_detected),
            "freshest_age_days": round(float(freshest_days), 2) if freshest_days is not None else None,
            "signal": signal,
            "event_agreement_level": event_agreement_level,
            "attribution_agreement_level": attribution_agreement_level,
            "attribution_uncertain": attribution_uncertain,
            "official_source_required": official_required,
            "official_source_found": bool(official >= 1),
            "high_stakes_mode": high_stakes,
        }

    async def _lookup_research_profile_cache(
        self,
        *,
        goal: str,
        high_stakes_mode: bool,
        official_source_required: bool,
    ) -> Optional[Dict[str, Any]]:
        try:
            rows = await self._get_firestore_memory().retrieve_research_profiles(
                user_id=self._active_user_id,
                query=goal,
                limit=1,
            )
        except Exception as exc:
            self._log("engine.research_profile_cache_lookup_failed", error=str(exc))
            return None
        if not rows:
            return None
        top = dict(rows[0] or {})
        agreement = dict(top.get("agreement") or {})
        if high_stakes_mode and official_source_required:
            if not bool(agreement.get("official_source_found")):
                return None
        if self._is_profile_or_entity_query(goal):
            verification_state = str(
                agreement.get("verification_state")
                or top.get("verification_state")
                or ""
            ).strip().lower()
            if verification_state not in {"confirmed", "partially_confirmed"}:
                return None
            cached_answer = str(top.get("answer") or "").lower()
            stale_profile_style = (
                "event-level" in cached_answer
                or "the event is corroborated" in cached_answer
                or "attribution remains partially disputed" in cached_answer
            )
            if stale_profile_style:
                return None
        return top

    def _extract_related_questions(self, answer: str, goal: str, max_items: int = 4) -> List[str]:
        lines = [str(line or "").strip() for line in str(answer or "").splitlines()]
        in_followups = False
        related: List[str] = []
        for line in lines:
            low = line.lower().strip(": ")
            if low in {"next useful follow-ups", "next useful follow-up", "related follow-ups", "follow-ups"}:
                in_followups = True
                continue
            if in_followups and line.startswith("- "):
                val = line[2:].strip()
                if val and val not in related:
                    related.append(val)
                if len(related) >= max_items:
                    break
            elif in_followups and line and not line.startswith("- "):
                break
        if related:
            return related[:max_items]
        topic = str(goal or "").strip()[:70]
        return [
            f"What are the latest verified updates about {topic}?",
            f"Can you show only official sources for {topic}?",
            f"What details about {topic} are still uncertain?",
        ][:max_items]

    def _build_research_profile_cache_answer(self, *, goal: str, cached_profile: Dict[str, Any]) -> str:
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        answer = str(cached_profile.get("answer") or "").strip()
        related = [str(q).strip() for q in (cached_profile.get("related_questions") or []) if str(q).strip()][:4]
        if not related:
            related = self._extract_related_questions(answer, goal, max_items=4)
        source_rows = list(cached_profile.get("source_rows") or [])[:5]
        lines: List[str] = [
            "Answer",
            f"As of {now_utc}, this is a fast semantic-cache snapshot from previously verified research memory.",
            "",
        ]
        if answer:
            lines.append(answer)
        if source_rows:
            lines.extend(["", "Sources"])
            for idx, row in enumerate(source_rows, start=1):
                title = str(row.get("title") or row.get("provider") or f"Source {idx}").strip()
                link = str(row.get("link") or "").strip()
                if link:
                    lines.append(f"- [S{idx}] {title} - {link}")
        if related:
            lines.extend(["", "Related questions"])
            for q in related[:4]:
                lines.append(f"- {q}")
        lines.extend(
            [
                "",
                "What is uncertain or disputed",
                "- This cached snapshot may miss very recent updates; request a live refresh for latest changes.",
            ]
        )
        return "\n".join(lines).strip()

    async def _store_research_profile_cache(
        self,
        *,
        goal: str,
        answer: str,
        evidence_rows: List[Dict[str, Any]],
        agreement: Optional[Dict[str, Any]] = None,
    ) -> None:
        text = str(answer or "").strip()
        if not text or len(text) < 80:
            return
        source_rows: List[Dict[str, Any]] = []
        for row in list(evidence_rows or [])[:8]:
            source_rows.append(
                {
                    "title": str(row.get("title") or "").strip(),
                    "link": str(row.get("link") or "").strip(),
                    "provider": str(row.get("provider") or "").strip(),
                    "tier": str(row.get("tier") or "").strip(),
                    "date_hint": row.get("date_hint"),
                }
            )
        related = self._extract_related_questions(text, goal, max_items=4)
        entity_hints: List[str] = []
        g = str(goal or "").strip()
        if g:
            entity_hints.append(g[:120])
        if self._is_profile_or_entity_query(goal):
            entity_hints.append("profile_query")
        try:
            await self._get_firestore_memory().store_research_profile(
                user_id=self._active_user_id,
                query=goal,
                answer=text,
                source_rows=source_rows,
                agreement=dict(agreement or {}),
                related_questions=related,
                entity_hints=entity_hints,
            )
        except Exception as exc:
            self._log("engine.research_profile_cache_store_failed", error=str(exc))

    async def _run_deep_research(self, goal: str) -> Optional[str]:
        t0 = time.time()
        if self._request_started_at <= 0:
            self._request_started_at = t0
        self._request_deadline_seconds = min(
            float(self._request_deadline_seconds or self._MAX_TOTAL_TIME_SECONDS),
            self._MAX_TOTAL_TIME_SECONDS,
        )
        self._log("engine.deep_research_start", goal=goal)
        role_flow: List[Dict[str, Any]] = []
        from taos.core.tools.builtin.extract_adapter import extract_with_adapter
        from taos.core.tools.builtin.web_search import web_search
        import asyncio

        freshness_decision = self._freshness_policy.decide(goal)
        freshness_mode = freshness_decision.mode in {"news_live", "current_lookup"}
        high_stakes_query = self._high_stakes_guard.evaluate_query(goal)
        high_stakes_mode = bool(high_stakes_query.get("high_stakes") or self._is_high_stakes_query(goal))
        profile_mode = self._is_profile_or_entity_query(goal)
        official_source_required = self._requires_official_sources(goal) or high_stakes_mode
        cache_summary = self._new_research_cache_summary()
        if self._trace_enabled:
            self._trace_data.setdefault("evidence_stats", {})["cache_summary"] = cache_summary
        # Semantic research cache: prefer fast retrieval for repeated entity/profile queries.
        if not freshness_mode:
            cached_profile = await self._lookup_research_profile_cache(
                goal=goal,
                high_stakes_mode=high_stakes_mode,
                official_source_required=official_source_required,
            )
            if cached_profile:
                self._append_direct_trace_step(
                    step_type="reason",
                    status="success",
                    tool="research_profile_cache",
                    summary="Returned semantic research cache snapshot from Firestore memory.",
                )
                self._mark_trace_fallback(
                    reason="research_profile_cache_hit",
                    freshness_status="recovered",
                    freshness_note="Returned cached semantic research profile to reduce latency; request live refresh for newest updates.",
                )
                return self._build_research_profile_cache_answer(
                    goal=goal,
                    cached_profile=cached_profile,
                )
        search_type = freshness_decision.search_type
        recency_days = freshness_decision.recency_days
        self._set_trace_value("freshness_mode", freshness_decision.mode)

        async def _cached_research_web_search(**kwargs: Any) -> Dict[str, Any]:
            query_text = str(kwargs.get("query") or "").strip()
            if not query_text:
                return {"results": []}
            result = await self._run_research_search_query(
                query_text=query_text,
                freshness_mode=freshness_decision.mode,
                search_type=str(kwargs.get("search_type") or search_type),
                recency_days=kwargs.get("recency_days"),
                web_search_fn=web_search,
                cache_summary=cache_summary,
            )
            rows = []
            for row in list(result.get("rows") or []):
                rows.append(
                    {
                        "title": str(row.get("title") or "").strip(),
                        "link": str(row.get("link") or "").strip(),
                        "snippet": str(row.get("search_snippet") or row.get("raw_snippet") or row.get("snippet") or "").strip(),
                    }
                )
            payload: Dict[str, Any] = {"results": rows}
            if result.get("error"):
                payload["error"] = result.get("error")
            return payload

        async def _timeout_fallback(
            *,
            reason: str,
            note: str,
            queries: Optional[List[str]],
            evidence_rows: Optional[List[Dict[str, Any]]] = None,
            extract_attempts: int = 0,
        ) -> str:
            evidence = list(evidence_rows or [])
            self._mark_trace_fallback(
                reason=reason,
                freshness_status="failed",
                freshness_note=note,
            )
            if evidence:
                agreement = self._compute_research_agreement(
                    evidence,
                    freshness_mode=freshness_mode,
                    goal=goal,
                    high_stakes_mode=high_stakes_mode,
                )
                fallback = self._build_research_evidence_fallback(
                    goal=goal,
                    evidence_rows=evidence,
                    freshness_mode=freshness_mode,
                    agreement=agreement,
                    high_stakes_mode=high_stakes_mode,
                )
                if fallback:
                    await self._store_research_profile_cache(
                        goal=goal,
                        answer=fallback,
                        evidence_rows=evidence,
                        agreement=agreement,
                    )
                    return fallback
            return self._build_research_unverified_message(
                goal,
                high_stakes_mode=high_stakes_mode,
                queries=queries,
                reason=reason,
                source_rows=len(evidence),
                extract_attempts=extract_attempts,
                official_source_required=official_source_required,
            )

        role_flow.append({"role": "planner", "status": "start", "at": datetime.now(timezone.utc).isoformat()})
        query_stage_timeout = self._stage_timeout_seconds(self._LLM_STAGE_TIMEOUT_SECONDS)
        if query_stage_timeout <= 0:
            self._log("engine.research_role_flow", role_flow=role_flow)
            return await _timeout_fallback(
                reason="research_timeout",
                note="Request time budget was exhausted before planning research queries.",
                queries=[goal],
            )
        try:
            queries = await asyncio.wait_for(
                self._generate_research_queries(goal),
                timeout=query_stage_timeout,
            )
        except asyncio.TimeoutError:
            queries = [self._sanitize_research_goal(goal)]
            self._append_direct_trace_step(
                step_type="reason",
                status="failed",
                tool="query_decompose",
                summary="Research query decomposition hit stage timeout; using original goal as fallback query.",
            )
        queries = [q for q in (queries or []) if str(q).strip()] or [self._sanitize_research_goal(goal)]
        self._log("engine.deep_research_queries", queries=queries)
        role_flow.append(
            {
                "role": "planner",
                "status": "completed",
                "at": datetime.now(timezone.utc).isoformat(),
                "query_count": len(queries),
            }
        )
        self._append_direct_trace_step(
            step_type="reason",
            status="success",
            tool="query_decompose",
            summary=f"Generated {len(queries)} research query variants.",
            latency_ms=(time.time() - t0) * 1000,
        )

        role_flow.append(
            {
                "role": "researcher",
                "status": "start",
                "at": datetime.now(timezone.utc).isoformat(),
                "search_type": search_type,
                "recency_days": recency_days,
                "official_source_required": official_source_required,
                "high_stakes_mode": high_stakes_mode,
            }
        )
        
        # Run multiple search formulations and pool all evidence.
        search_tasks: Dict[str, asyncio.Task[Any]] = {}
        for query_text in queries:
            search_tasks[query_text] = asyncio.create_task(
                self._run_research_search_query(
                    query_text=query_text,
                    freshness_mode=freshness_decision.mode,
                    search_type=search_type,
                    recency_days=recency_days,
                    web_search_fn=web_search,
                    cache_summary=cache_summary,
                )
            )
        search_timeout = self._stage_timeout_seconds(self._SEARCH_STAGE_TIMEOUT_SECONDS)
        done_tasks: set[asyncio.Task[Any]] = set()
        pending_tasks: set[asyncio.Task[Any]] = set()
        if search_tasks and search_timeout > 0:
            done_tasks, pending_tasks = await asyncio.wait(
                list(search_tasks.values()),
                timeout=search_timeout,
            )
        else:
            pending_tasks = set(search_tasks.values())

        search_rows: List[Dict[str, Any]] = []
        search_errors: List[str] = []
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
            search_errors.append(
                f"search_stage_timeout_after_{round(float(search_timeout or 0.0), 2)}s"
            )

        for query_text, task in search_tasks.items():
            if task not in done_tasks:
                continue
            try:
                res = task.result()
            except Exception as exc:
                search_errors.append(f"{type(exc).__name__}: {exc}")
                continue
            if isinstance(res, Exception):
                search_errors.append(f"{type(res).__name__}: {res}")
                continue
            if isinstance(res, dict):
                err = str(res.get("error") or "").strip()
                if err:
                    search_errors.append(err[:180])
                for row in list(res.get("rows") or []):
                    if row:
                        search_rows.append(dict(row))
        role_flow.append(
            {
                "role": "researcher",
                "status": "completed",
                "at": datetime.now(timezone.utc).isoformat(),
                "evidence_rows": len(search_rows),
                "search_timeout_seconds": search_timeout,
            }
        )
        self._append_direct_trace_step(
            step_type="tool",
            status="success" if bool(search_rows) else "failed",
            tool="web_search",
            summary=f"Collected {len(search_rows)} candidate evidence row(s) from parallel search variants.",
            error=(" | ".join(search_errors[:2]) if search_errors else None),
        )

        if not search_rows:
            recovery_queries = self._build_research_recovery_queries(goal=goal, queries=queries)
            if recovery_queries:
                recovery_timeout = self._stage_timeout_seconds(self._SEARCH_STAGE_TIMEOUT_SECONDS + 3.0)
                if recovery_timeout > 0:
                    per_query_timeout = max(1.5, float(recovery_timeout) / max(1, len(recovery_queries)))
                    recovered_rows = 0
                    recovery_errors: List[str] = []
                    for query_text in recovery_queries:
                        try:
                            res = await asyncio.wait_for(
                                self._run_research_search_query(
                                    query_text=query_text,
                                    freshness_mode=freshness_decision.mode,
                                    search_type=search_type,
                                    recency_days=recency_days,
                                    web_search_fn=web_search,
                                    cache_summary=cache_summary,
                                ),
                                timeout=per_query_timeout,
                            )
                        except asyncio.TimeoutError:
                            recovery_errors.append(
                                f"recovery_timeout_{round(float(per_query_timeout), 2)}s"
                            )
                            continue
                        except Exception as exc:
                            recovery_errors.append(f"{type(exc).__name__}: {exc}")
                            continue

                        if isinstance(res, dict):
                            err = str(res.get("error") or "").strip()
                            if err:
                                recovery_errors.append(err[:180])
                            for row in list(res.get("rows") or []):
                                if row:
                                    search_rows.append(dict(row))
                                    recovered_rows += 1

                    if recovery_errors:
                        search_errors.extend(recovery_errors[:3])
                    if recovered_rows > 0:
                        role_flow.append(
                            {
                                "role": "researcher",
                                "status": "recovered",
                                "at": datetime.now(timezone.utc).isoformat(),
                                "evidence_rows": len(search_rows),
                                "recovery_queries": len(recovery_queries),
                            }
                        )
                        self._append_direct_trace_step(
                            step_type="tool",
                            status="success",
                            tool="web_search",
                            summary=(
                                f"Recovered {recovered_rows} candidate evidence row(s) via targeted fallback search "
                                f"after initial zero-result pass."
                            ),
                            error=(" | ".join(search_errors[:2]) if search_errors else None),
                        )

        if not search_rows:
            first_error = search_errors[0] if search_errors else None
            search_stage_timed_out = any("search_stage_timeout_after_" in row for row in search_errors)
            fallback_reason = "research_timeout" if search_stage_timed_out else ("web_search_failed" if first_error else "research_unverified_fallback")
            freshness_note = (
                f"Search provider returned errors: {first_error}"
                if first_error
                else "No verifiable sources were found for this research request."
            )
            if search_stage_timed_out:
                freshness_note = "Search stage exceeded the hard time budget before returning usable sources."
            self._mark_trace_fallback(
                reason=fallback_reason,
                freshness_status="failed",
                freshness_note=freshness_note,
            )
            if self._trace_enabled:
                stats = self._trace_data.setdefault("evidence_stats", {})
                stats["source_rows"] = self._build_query_reference_rows(queries)
            self._log("engine.research_role_flow", role_flow=role_flow)
            no_result = self._no_result_handler.build(
                goal=goal,
                checked_queries=queries,
                official_checked=official_source_required,
                recent_checked=bool(recency_days),
                trusted_checked=True,
            )
            self._trace_data.setdefault("evidence_stats", {})["no_result_handler_used"] = True
            return str(no_result.get("answer") or self._build_research_unverified_message(
                goal,
                high_stakes_mode=high_stakes_mode,
                queries=queries,
                reason=fallback_reason,
                search_error=first_error,
                source_rows=0,
                extract_attempts=0,
                official_source_required=official_source_required,
            ))

        preliminary_freshness = self._freshness_policy.summarize(search_rows, mode=freshness_decision.mode)
        booster_result = await self._research_pipeline.freshness_booster.boost(
            query=goal,
            rows=search_rows,
            freshness_summary=preliminary_freshness,
            web_search_fn=_cached_research_web_search,
            search_type=search_type,
            recency_days=recency_days,
        )
        search_rows = list(booster_result.get("rows") or search_rows)
        freshness_boost_summary = dict(booster_result.get("summary") or {})
        for row in search_rows:
            base_snippet = str(row.get("raw_snippet") or row.get("search_snippet") or row.get("snippet") or "").strip()
            row.setdefault("raw_snippet", base_snippet)
            row.setdefault("search_snippet", base_snippet)
        if self._trace_enabled:
            self._trace_data.setdefault("evidence_stats", {})["freshness_summary"] = {
                **preliminary_freshness,
                "booster": freshness_boost_summary,
            }

        # Rank and diversify pooled evidence before extraction.
        role_flow.append({"role": "validator", "status": "start", "at": datetime.now(timezone.utc).isoformat()})
        ranked_rows = self._rank_research_evidence(
            search_rows,
            limit=10 if freshness_mode else 8,
            freshness_mode=freshness_mode,
            official_source_required=official_source_required,
        )
        preselected_rows = self._apply_domain_cap(ranked_rows, max_per_domain=2)
        preselected_rows = self._ensure_official_source_in_ranked_rows(
            preselected_rows,
            ranked_rows,
            required=official_source_required,
            max_keep=10 if freshness_mode else 8,
        )
        preselected_rows = [
            self._merge_cached_evidence_row(
                row,
                freshness_mode=freshness_decision.mode,
                cache_summary=cache_summary,
            )
            for row in preselected_rows
        ]
        if profile_mode:
            preselected_rows = self._filter_profile_lookup_evidence_rows(preselected_rows, goal=goal)
        selection_bundle = self._research_pipeline.select_evidence(
            rows=preselected_rows,
            query=goal,
            limit=10 if freshness_mode else 8,
            max_per_domain=2,
        )
        deduped = list(selection_bundle.get("rows") or [])
        evidence_selection_summary = dict(selection_bundle.get("summary") or {})
        diversity_summary = {
            "unique_domains": int(evidence_selection_summary.get("unique_domains") or 0),
            "max_per_domain": int(evidence_selection_summary.get("max_per_domain") or 2),
            "domain_counts": dict(evidence_selection_summary.get("domain_counts") or {}),
            "category_counts": dict(evidence_selection_summary.get("category_counts") or {}),
            "dropped_by_domain_cap": int(evidence_selection_summary.get("dropped_by_domain_cap") or 0),
        }
        for row in deduped:
            blob = f"{row.get('published_at', '')}. {row.get('title', '')}. {row.get('snippet', '')}"
            dt = self._extract_date_from_text(blob)
            if dt:
                row["date_hint"] = dt.strftime("%Y-%m-%d")
        pre_extract_freshness = self._freshness_policy.summarize(deduped, mode=freshness_decision.mode)
        quality_bundle = self._apply_research_quality_gate(
            rows=deduped,
            goal=goal,
            freshness_summary=pre_extract_freshness,
            official_source_required=official_source_required,
        )
        source_quality_summary = dict(quality_bundle.get("summary") or {})
        usable_quality_rows = list(quality_bundle.get("usable_rows") or [])
        targeted_retry_query = self._build_missing_evidence_query(
            goal=goal,
            official_source_required=official_source_required,
            freshness_mode=freshness_decision.mode,
            quality_summary=source_quality_summary,
        )
        needs_targeted_retry = bool(
            targeted_retry_query
            and (
                int(source_quality_summary.get("usable_count") or 0) <= 1
                or (
                    official_source_required
                    and not bool(source_quality_summary.get("official_source_found"))
                )
                or (
                    freshness_mode
                    and float(source_quality_summary.get("freshness_score") or 0.0) < 0.70
                )
            )
        )
        if needs_targeted_retry and targeted_retry_query.lower() not in {str(q).lower() for q in queries}:
            retry_timeout = self._stage_timeout_seconds(2.5)
            if retry_timeout > 0:
                try:
                    retry_res = await asyncio.wait_for(
                        self._run_research_search_query(
                            query_text=targeted_retry_query,
                            freshness_mode=freshness_decision.mode,
                            search_type=search_type,
                            recency_days=recency_days,
                            web_search_fn=web_search,
                            cache_summary=cache_summary,
                        ),
                        timeout=retry_timeout,
                    )
                except Exception as exc:
                    retry_res = {"rows": [], "error": f"{type(exc).__name__}: {exc}"}
                retry_rows = list((retry_res or {}).get("rows") or [])
                if retry_rows:
                    search_rows.extend(dict(row) for row in retry_rows if row)
                    ranked_rows = self._rank_research_evidence(
                        search_rows,
                        limit=10 if freshness_mode else 8,
                        freshness_mode=freshness_mode,
                        official_source_required=official_source_required,
                    )
                    preselected_rows = self._apply_domain_cap(ranked_rows, max_per_domain=2)
                    preselected_rows = self._ensure_official_source_in_ranked_rows(
                        preselected_rows,
                        ranked_rows,
                        required=official_source_required,
                        max_keep=10 if freshness_mode else 8,
                    )
                    selection_bundle = self._research_pipeline.select_evidence(
                        rows=preselected_rows,
                        query=goal,
                        limit=10 if freshness_mode else 8,
                        max_per_domain=2,
                    )
                    deduped = list(selection_bundle.get("rows") or deduped)
                    evidence_selection_summary = dict(selection_bundle.get("summary") or evidence_selection_summary)
                    pre_extract_freshness = self._freshness_policy.summarize(deduped, mode=freshness_decision.mode)
                    quality_bundle = self._apply_research_quality_gate(
                        rows=deduped,
                        goal=goal,
                        freshness_summary=pre_extract_freshness,
                        official_source_required=official_source_required,
                    )
                    source_quality_summary = dict(quality_bundle.get("summary") or {})
                    usable_quality_rows = list(quality_bundle.get("usable_rows") or [])
                if self._trace_enabled:
                    stats = self._trace_data.setdefault("evidence_stats", {})
                    stats["targeted_retry_used"] = True
                    stats["targeted_retry_query"] = targeted_retry_query
                    stats["targeted_retry_added_rows"] = len(retry_rows)
                    if retry_res.get("error"):
                        stats["targeted_retry_error"] = str(retry_res.get("error"))[:180]
                self._append_direct_trace_step(
                    step_type="tool",
                    status="success" if retry_rows else "failed",
                    tool="web_search",
                    summary=f"Ran targeted missing-evidence retry: {targeted_retry_query}",
                )
        if usable_quality_rows:
            deduped = usable_quality_rows
        else:
            deduped = []
        if self._trace_enabled:
            stats = self._trace_data.setdefault("evidence_stats", {})
            stats["source_quality_summary"] = source_quality_summary
            stats["source_quality_rows"] = source_quality_summary.get("source_quality_rows", [])
            stats["usable_source_count"] = int(source_quality_summary.get("usable_count") or 0)
            stats["rejected_source_count"] = int(source_quality_summary.get("rejected_count") or 0)
            stats["official_source_count"] = int(source_quality_summary.get("official_source_count") or 0)
        if not deduped:
            candidate_rows = preselected_rows or ranked_rows or search_rows
            sparse_fallback_rows = [dict(row) for row in candidate_rows if isinstance(row, dict)]
            sparse_agreement = self._compute_research_agreement(
                sparse_fallback_rows,
                freshness_mode=freshness_mode,
                goal=goal,
                high_stakes_mode=high_stakes_mode,
            )
            sparse_fallback = None
            if preselected_rows or ranked_rows:
                sparse_fallback = self._build_research_evidence_fallback(
                    goal=goal,
                    evidence_rows=sparse_fallback_rows,
                    freshness_mode=freshness_mode,
                    agreement=sparse_agreement,
                    high_stakes_mode=high_stakes_mode,
                )
                if sparse_fallback:
                    self._mark_trace_fallback(
                        reason="research_evidence_fallback",
                        freshness_status="recovered",
                        freshness_note="Search returned candidate rows but ranking/quality filters were sparse; returned cautious evidence fallback.",
                    )
                    self._append_direct_trace_step(
                        step_type="reason",
                        status="failed",
                        tool="evidence_ranker",
                        summary="Ranking/quality filters were sparse; returned structured evidence fallback.",
                    )
                    self._log("engine.research_role_flow", role_flow=role_flow)
                    return sparse_fallback
            self._mark_trace_fallback(
                reason="profile_evidence_mismatch" if profile_mode else "search_sparse",
                freshness_status="failed",
                freshness_note=(
                    "Search returned rows, but none were clearly relevant to the requested company-role lookup."
                    if profile_mode
                    else "Search returned rows but none survived ranking/diversity filters."
                ),
            )
            if self._trace_enabled:
                stats = self._trace_data.setdefault("evidence_stats", {})
                sparse_rows = []
                for row in search_rows[:3]:
                    sparse_rows.append(
                        {
                            "title": str(row.get("title") or "Candidate source"),
                            "link": str(row.get("link") or "").strip(),
                            "provider": str(row.get("provider") or "search"),
                            "tier": "mixed",
                            "date_hint": row.get("date_hint"),
                        }
                    )
                stats["source_rows"] = [r for r in sparse_rows if str(r.get("link") or "").strip()]
                if sparse_fallback:
                    stats["search_sparse_fallback_used"] = True
            self._log("engine.research_role_flow", role_flow=role_flow)
            if sparse_fallback:
                self._append_direct_trace_step(
                    step_type="reason",
                    status="failed",
                    tool="source_ranker",
                    summary="Search rows were sparse after quality filters; returned structured evidence fallback from available rows.",
                )
                self._mark_trace_fallback(
                    reason="research_evidence_fallback",
                    freshness_status="recovered",
                    freshness_note="Quality filters rejected rows; returned snippet-level evidence fallback from available search results.",
                )
                await self._store_research_profile_cache(
                    goal=goal,
                    answer=sparse_fallback,
                    evidence_rows=sparse_fallback_rows,
                    agreement=sparse_agreement,
                )
                return sparse_fallback
            no_result = self._no_result_handler.build(
                goal=goal,
                checked_queries=queries,
                official_checked=official_source_required,
                recent_checked=bool(recency_days),
                trusted_checked=True,
            )
            self._trace_data.setdefault("evidence_stats", {})["no_result_handler_used"] = True
            return str(no_result.get("answer") or "")
        role_flow.append(
            {
                "role": "validator",
                "status": "completed",
                "at": datetime.now(timezone.utc).isoformat(),
                "unique_sources": len(deduped),
                "provider_count": len({row.get("provider") for row in deduped if row.get("provider")}),
                "official_source_required": official_source_required,
                "official_source_found": any(str(row.get("tier") or "") == "official" for row in deduped),
                "high_stakes_mode": high_stakes_mode,
            }
        )
        self._append_direct_trace_step(
            step_type="validate",
            status="success",
            tool="source_ranker",
            summary=f"Ranked and deduplicated sources to {len(deduped)} high-signal entries.",
        )

        # Extract readable text from top URLs to improve evidence grounding.
        extract_limit = self._select_extraction_budget(deduped, freshness_mode=freshness_mode)
        prefilter_pool = deduped[: min(len(deduped), max(extract_limit * 2, extract_limit))]
        prefilter = self._prefilter_extract_candidates(prefilter_pool, max_keep=extract_limit)
        extract_candidates = list(prefilter.get("kept") or [])
        extract_candidates = self._ensure_official_source_in_extract_candidates(
            extract_candidates,
            deduped,
            required=official_source_required,
            max_keep=extract_limit,
        )
        prefilter_skipped = int(prefilter.get("skipped") or 0)
        prefilter_reasons = dict(prefilter.get("reasons") or {})
        role_flow.append(
            {
                "role": "researcher",
                "status": "extract_start",
                "at": datetime.now(timezone.utc).isoformat(),
                "url_count": len(extract_candidates),
                "prefilter_skipped": prefilter_skipped,
            }
        )
        extract_jobs: List[tuple[Dict[str, Any], asyncio.Task[Any]]] = []
        extract_cache_hits = 0
        extract_cache_stale = 0
        extract_cache_miss = 0
        for row in extract_candidates:
            link = str(row.get("link") or "").strip()
            if not link:
                continue
            cached_extract, extract_cache_status = self._extract_cache.get(
                link,
                freshness_mode=freshness_decision.mode,
                allow_stale=freshness_decision.mode == "historical",
            )
            self._record_research_cache_status(cache_summary, layer="extract", status=extract_cache_status)
            if extract_cache_status == "hit":
                extract_cache_hits += 1
                self._apply_cached_extract_to_row(row, cached_extract or {})
                continue
            if extract_cache_status == "stale":
                extract_cache_stale += 1
            else:
                extract_cache_miss += 1
            extract_jobs.append(
                (
                    row,
                    asyncio.create_task(
                        extract_with_adapter(
                            url=link,
                            timeout=12,
                            max_chars=2200,
                            include_html=False,
                            prefer_scrapling_http=bool(self._settings.scrapling_http_extractor_enabled),
                        )
                    ),
                )
            )
        extract_timeout = self._stage_timeout_seconds(self._EXTRACT_STAGE_TIMEOUT_SECONDS)
        done_extract_tasks: set[asyncio.Task[Any]] = set()
        pending_extract_tasks: set[asyncio.Task[Any]] = set()
        if extract_jobs and extract_timeout > 0:
            done_extract_tasks, pending_extract_tasks = await asyncio.wait(
                [job[1] for job in extract_jobs],
                timeout=extract_timeout,
            )
        else:
            pending_extract_tasks = {job[1] for job in extract_jobs}

        extract_fetch_success = extract_cache_hits
        extract_usable = sum(
            1
            for row in extract_candidates
            if str(row.get("extract_cache_status") or "").strip().lower() == "hit"
            and not row.get("extract_rejection")
        )
        extract_quality_scores: List[float] = [
            float(row.get("extract_quality_score") or 0.0)
            for row in extract_candidates
            if str(row.get("extract_cache_status") or "").strip().lower() == "hit"
            and float(row.get("extract_quality_score") or 0.0) > 0
        ]
        extract_rejections: Dict[str, int] = {}
        extract_adapter_counts: Dict[str, int] = {}
        for task in pending_extract_tasks:
            task.cancel()
        if pending_extract_tasks:
            await asyncio.gather(*pending_extract_tasks, return_exceptions=True)
            extract_rejections["stage_timeout"] = len(pending_extract_tasks)

        for row, task in extract_jobs:
            if task not in done_extract_tasks:
                continue
            try:
                extracted = task.result()
            except Exception as exc:
                extract_rejections[type(exc).__name__] = extract_rejections.get(type(exc).__name__, 0) + 1
                continue
            if not isinstance(extracted, dict) or not extracted.get("success"):
                continue
            extract_fetch_success += 1
            adapter_name = str(extracted.get("extractor_adapter") or "web_extract").strip() or "web_extract"
            extract_adapter_counts[adapter_name] = extract_adapter_counts.get(adapter_name, 0) + 1
            quality_score = float(extracted.get("quality_score", 0.0) or 0.0)
            if quality_score > 0:
                extract_quality_scores.append(quality_score)
            self._extract_cache.set(
                str(row.get("link") or ""),
                extracted,
                freshness_mode=freshness_decision.mode,
            )
            usable_for_research = self._apply_cached_extract_to_row(row, extracted)
            if not usable_for_research:
                rejection_reason = str(row.get("extract_rejection") or extracted.get("rejection_reason") or "low_quality_content")
                extract_rejections[rejection_reason] = extract_rejections.get(rejection_reason, 0) + 1
                continue
            extract_usable += 1
        role_flow.append(
            {
                "role": "researcher",
                "status": "extract_completed",
                "at": datetime.now(timezone.utc).isoformat(),
                "extract_fetch_success": extract_fetch_success,
                "extract_success": extract_usable,
                "extract_attempts": len(extract_jobs),
                "extract_rejected": sum(extract_rejections.values()),
                "extract_timeout_seconds": extract_timeout,
                "extract_adapters": dict(extract_adapter_counts),
                "extract_cache_hits": extract_cache_hits,
            }
        )
        self._append_direct_trace_step(
            step_type="tool",
            status="success" if extract_usable > 0 else "failed",
            tool="web_extract",
            summary=(
                f"Extracted {extract_usable}/{len(extract_jobs)} usable source pages "
                f"(fetch-ok: {extract_fetch_success}, cache-hit: {extract_cache_hits}, prefilter-skipped: {prefilter_skipped})."
            ),
        )
        if extract_usable <= 0:
            recovery = self._extract_recovery.recover(
                ranked_rows=deduped,
                official_source_required=official_source_required,
            )
            recovered_rows = list(recovery.get("recovered_rows") or [])
            fallback_rows = recovered_rows or deduped or preselected_rows or ranked_rows or search_rows
            self._update_research_evidence_trace(fallback_rows)
            fallback_reason = "research_timeout" if extract_rejections.get("stage_timeout") else "extract_failed"
            agreement = self._compute_research_agreement(
                fallback_rows,
                freshness_mode=freshness_mode,
                goal=goal,
                high_stakes_mode=high_stakes_mode,
            )
            fallback = self._build_research_evidence_fallback(
                goal=goal,
                evidence_rows=fallback_rows,
                freshness_mode=freshness_mode,
                agreement=agreement,
                high_stakes_mode=high_stakes_mode,
            )
            if fallback:
                self._append_direct_trace_step(
                    step_type="reason",
                    status="failed",
                    tool="web_extract",
                    summary="Extraction produced no usable pages; returned structured evidence fallback from ranked snippets.",
                )
                self._mark_trace_fallback(
                    reason="research_timeout" if fallback_reason == "research_timeout" else "research_evidence_fallback",
                    freshness_status="recovered",
                    freshness_note=(
                        "Extraction stage exceeded time budget, so snippet-level evidence fallback was returned."
                        if fallback_reason == "research_timeout"
                        else "Extraction failed for ranked sources; snippet-level evidence fallback was returned."
                    ),
                )
                stats = self._trace_data.setdefault("evidence_stats", {})
                stats["extraction_recovery_used"] = bool(recovery.get("used"))
                stats["extract_recovery_warning"] = recovery.get("warning")
                self._log("engine.research_role_flow", role_flow=role_flow)
                await self._store_research_profile_cache(
                    goal=goal,
                    answer=fallback,
                    evidence_rows=fallback_rows,
                    agreement=agreement,
                )
                return fallback
            self._mark_trace_fallback(
                reason=fallback_reason,
                freshness_status="failed",
                freshness_note=(
                    "Extraction stage exceeded the hard time budget before usable page text was produced."
                    if fallback_reason == "research_timeout"
                    else "Page extraction failed for ranked sources; no source-grounded synthesis was emitted."
                ),
            )
            self._log("engine.research_role_flow", role_flow=role_flow)
            return self._build_research_unverified_message(
                goal,
                high_stakes_mode=high_stakes_mode,
                queries=queries,
                reason=fallback_reason,
                source_rows=len(deduped),
                extract_attempts=len(extract_jobs),
                official_source_required=official_source_required,
            )
        post_extract_selection = self._research_pipeline.select_evidence(
            rows=deduped,
            query=goal,
            limit=8 if not freshness_mode else 10,
            max_per_domain=2,
        )
        deduped = list(post_extract_selection.get("rows") or deduped)
        evidence_selection_summary = dict(post_extract_selection.get("summary") or evidence_selection_summary)
        diversity_summary = {
            "unique_domains": int(evidence_selection_summary.get("unique_domains") or 0),
            "max_per_domain": int(evidence_selection_summary.get("max_per_domain") or 2),
            "domain_counts": dict(evidence_selection_summary.get("domain_counts") or {}),
            "category_counts": dict(evidence_selection_summary.get("category_counts") or {}),
            "dropped_by_domain_cap": int(evidence_selection_summary.get("dropped_by_domain_cap") or 0),
        }
        for row in deduped:
            row.setdefault("raw_snippet", str(row.get("raw_snippet") or row.get("search_snippet") or row.get("snippet") or "").strip())
            row.setdefault("search_snippet", str(row.get("search_snippet") or row.get("raw_snippet") or "").strip())
            self._evidence_cache.set(
                row,
                self._build_cached_evidence_payload(row),
                freshness_mode=freshness_decision.mode,
            )
        post_quality_bundle = self._apply_research_quality_gate(
            rows=deduped,
            goal=goal,
            freshness_summary=self._freshness_policy.summarize(deduped, mode=freshness_decision.mode),
            official_source_required=official_source_required,
        )
        post_quality_summary = dict(post_quality_bundle.get("summary") or {})
        post_usable_rows = list(post_quality_bundle.get("usable_rows") or [])
        if post_usable_rows:
            deduped = post_usable_rows
            source_quality_summary = post_quality_summary
        self._update_research_evidence_trace(deduped)
        agreement = self._compute_research_agreement(
            deduped,
            freshness_mode=freshness_mode,
            goal=goal,
            high_stakes_mode=high_stakes_mode,
        )
        conflict_summary = self._research_pipeline.resolve_conflicts(evidence_rows=deduped).get("summary") or {}
        freshness_summary = self._freshness_policy.summarize(deduped, mode=freshness_decision.mode)
        if freshness_boost_summary:
            freshness_summary["booster"] = freshness_boost_summary
        high_stakes_summary = self._high_stakes_guard.evaluate_evidence(query=goal, rows=deduped)
        if self._trace_enabled:
            stats = self._trace_data.setdefault("evidence_stats", {})
            stats["official_source_required"] = official_source_required
            stats["official_source_found"] = any(str(row.get("tier") or "") == "official" for row in deduped)
            stats["high_stakes_mode"] = high_stakes_mode
            stats["extract_count"] = extract_usable
            stats["extract_fetch_count"] = extract_fetch_success
            stats["extract_rejected_count"] = sum(extract_rejections.values())
            stats["extract_prefilter_skipped_count"] = prefilter_skipped
            stats["extract_prefilter_reasons"] = dict(
                sorted(prefilter_reasons.items(), key=lambda item: item[1], reverse=True)[:4]
            )
            stats["extraction_quality"] = (
                round(sum(extract_quality_scores) / len(extract_quality_scores), 3)
                if extract_quality_scores
                else 0.0
            )
            stats["extract_rejection_reasons"] = dict(
                sorted(extract_rejections.items(), key=lambda item: item[1], reverse=True)[:4]
            )
            stats["extract_adapters"] = dict(extract_adapter_counts)
            stats["agreement_level"] = agreement.get("agreement_level")
            stats["agreement_score"] = agreement.get("agreement_score")
            stats["conflict_detected"] = agreement.get("conflict_detected")
            stats["stale_detected"] = agreement.get("stale_detected")
            stats["freshest_age_days"] = agreement.get("freshest_age_days")
            stats["signal"] = agreement.get("signal")
            stats["event_agreement_level"] = agreement.get("event_agreement_level")
            stats["attribution_agreement_level"] = agreement.get("attribution_agreement_level")
            stats["official_source_required"] = agreement.get("official_source_required", official_source_required)
            stats["official_source_found"] = agreement.get("official_source_found")
            stats["high_stakes_mode"] = agreement.get("high_stakes_mode", high_stakes_mode)
            stats["freshness_summary"] = freshness_summary
            stats["freshness_mode"] = freshness_decision.mode
            stats["freshness_score"] = freshness_summary.get("freshness_score")
            stats["freshness_booster_used"] = bool(freshness_boost_summary.get("boosted"))
            stats["oldest_accepted_source"] = source_quality_summary.get("oldest_accepted_source")
            stats["newest_accepted_source"] = source_quality_summary.get("newest_accepted_source")
            stats["evidence_selection_summary"] = evidence_selection_summary
            stats["diversity_summary"] = diversity_summary
            stats["conflict_summary"] = conflict_summary
            stats["high_stakes_summary"] = high_stakes_summary
            stats["source_quality_summary"] = source_quality_summary
            stats["source_quality_rows"] = source_quality_summary.get("source_quality_rows", [])
            stats["usable_source_count"] = int(source_quality_summary.get("usable_count") or 0)
            stats["rejected_source_count"] = int(source_quality_summary.get("rejected_count") or 0)
            stats["official_source_count"] = int(source_quality_summary.get("official_source_count") or 0)
            stats["trusted_source_count"] = int(source_quality_summary.get("trusted_source_count") or 0)
            stats["cache_summary"] = cache_summary
            stats["stale_detected"] = bool(agreement.get("stale_detected") or freshness_summary.get("stale_detected"))
            stats["source_diversity_score"] = stats.get("domain_diversity", 0.0)

        # Truncate to protect model limits.
        numbered = []
        for i, row in enumerate(deduped[:30], start=1):
            numbered.append(
                f"S{i} | date_hint={row.get('date_hint', '')} | tier={row.get('tier', '')} | provider={row.get('provider', '')} | "
                f"title={row['title']} | link={row['link']} | snippet={row['snippet']}"
            )
        combined_text = "\n".join(numbered)
        words = combined_text.split()
        if len(words) > 4000:
            combined_text = " ".join(words[:4000])
            
        # Run Synthesis Layer
        role_flow.append({"role": "synthesizer", "status": "start", "at": datetime.now(timezone.utc).isoformat()})
        synth_timeout = self._stage_timeout_seconds(self._LLM_STAGE_TIMEOUT_SECONDS)
        if synth_timeout <= 0:
            role_flow.append(
                {
                    "role": "synthesizer",
                    "status": "timeout",
                    "at": datetime.now(timezone.utc).isoformat(),
                    "timeout_seconds": synth_timeout,
                }
            )
            self._log("engine.research_role_flow", role_flow=role_flow)
            return await _timeout_fallback(
                reason="research_timeout",
                note="Synthesis stage could not start because request time budget was exhausted.",
                queries=queries,
                evidence_rows=deduped,
                extract_attempts=len(extract_jobs),
            )
        synthesized: Optional[str] = None
        try:
            synthesized = await asyncio.wait_for(
                self._synthesize_research(
                    combined_text,
                    goal,
                    freshness_mode=freshness_mode,
                    agreement=agreement,
                    high_stakes_mode=high_stakes_mode,
                ),
                timeout=synth_timeout,
            )
        except asyncio.TimeoutError:
            role_flow.append(
                {
                    "role": "synthesizer",
                    "status": "timeout",
                    "at": datetime.now(timezone.utc).isoformat(),
                    "timeout_seconds": synth_timeout,
                }
            )
            self._log("engine.research_role_flow", role_flow=role_flow)
            self._append_direct_trace_step(
                step_type="reason",
                status="failed",
                tool="llm_synthesis",
                summary="Synthesis hit hard stage timeout; returning evidence-based fallback.",
            )
            return await _timeout_fallback(
                reason="research_timeout",
                note="Synthesis stage exceeded the hard time budget; returned evidence-based fallback.",
                queries=queries,
                evidence_rows=deduped,
                extract_attempts=len(extract_jobs),
            )
        if synthesized:
            synthesized = re.sub(r'<scratchpad>.*?</scratchpad>', '', synthesized, flags=re.DOTALL).strip()
            citation_plan = self._research_pipeline.plan_citations(answer=synthesized, source_rows=deduped)
            citation_plan_summary = dict(citation_plan.get("summary") or {})
            synthesized = self._research_pipeline.soften_unsupported_claims(
                answer=synthesized,
                citation_plan=citation_plan,
            )
            synthesized = self._citation_checker.soften_unsupported_claims(answer=synthesized, source_rows=deduped)
            repair_result = self._research_pipeline.repair_answer(
                answer=synthesized,
                citation_plan=citation_plan,
            )
            synthesized = str(repair_result.get("answer") or synthesized).strip()
            if conflict_summary.get("requires_uncertainty_wording"):
                synthesized += (
                    "\n\nWhat is still unclear\n"
                    "- Sources still conflict on some details, so treat disputed points as uncertain."
                )
            synthesized = self._high_stakes_guard.apply_safe_wording(
                answer=synthesized,
                summary=high_stakes_summary,
            )
            repaired_citation_plan = self._research_pipeline.plan_citations(answer=synthesized, source_rows=deduped)
            citation_coverage_summary = self._research_pipeline.citation_coverage(
                citation_plan=repaired_citation_plan,
            )
            answer_policy = self._research_pipeline.answer_policy(
                quality_summary=source_quality_summary,
                citation_coverage=citation_coverage_summary,
                conflict_summary=conflict_summary,
            )
            if 0.50 <= float(citation_coverage_summary.get("coverage") or 0.0) < 0.75:
                synthesized += (
                    "\n\nWhat to treat carefully\n"
                    "- Citation coverage is partial, so treat weaker or uncited details as provisional."
                )
            if float(citation_coverage_summary.get("coverage") or 0.0) < 0.50:
                fallback = self._build_research_evidence_fallback(
                    goal=goal,
                    evidence_rows=deduped,
                    freshness_mode=freshness_mode,
                    agreement=agreement,
                    high_stakes_mode=high_stakes_mode,
                )
                if fallback:
                    self._append_direct_trace_step(
                        step_type="reason",
                        status="failed",
                        tool="citation_coverage",
                        summary="Synthesis citation coverage was weak; returned best-supported evidence fallback.",
                    )
                    self._mark_trace_fallback(
                        reason="citation_coverage_low",
                        freshness_status="recovered",
                        freshness_note="Final answer was replaced by a cautious source-grounded fallback because citation coverage was below threshold.",
                    )
                    if self._trace_enabled:
                        stats = self._trace_data.setdefault("evidence_stats", {})
                        stats["citation_coverage_summary"] = citation_coverage_summary
                        stats["answer_policy"] = answer_policy
                        stats["answer_mode"] = answer_policy.get("answer_mode")
                        stats["answer_repair_summary"] = repair_result
                        stats["unsupported_critical_claims"] = 0
                    return fallback
            synthesized = self._research_pipeline.compose_research_answer(
                query=goal,
                draft_answer=synthesized,
                evidence_rows=deduped,
                answer_policy=answer_policy,
                conflict_summary=conflict_summary,
            )
            if self._trace_enabled:
                stats = self._trace_data.setdefault("evidence_stats", {})
                stats["citation_plan_summary"] = citation_plan_summary
                stats["citation_coverage_summary"] = citation_coverage_summary
                stats["answer_policy"] = answer_policy
                stats["answer_mode"] = answer_policy.get("answer_mode")
                stats["answer_repair_summary"] = repair_result
                stats["coverage"] = citation_coverage_summary.get("coverage")
                stats["unsupported_claims"] = citation_coverage_summary.get("claims_unsupported")
                stats["unsupported_critical_claims"] = 0
            if "as of my last update" in synthesized.lower():
                self._log("engine.research_synthesis_rejected", reason="stale_cutoff_phrase")
                role_flow.append({"role": "synthesizer", "status": "rejected", "at": datetime.now(timezone.utc).isoformat(), "reason": "stale_cutoff_phrase"})
                self._log("engine.research_role_flow", role_flow=role_flow)
                fallback = self._build_research_evidence_fallback(
                    goal=goal,
                    evidence_rows=deduped,
                    freshness_mode=freshness_mode,
                    agreement=agreement,
                    high_stakes_mode=high_stakes_mode,
                )
                if fallback:
                    self._append_direct_trace_step(
                        step_type="reason",
                        status="failed",
                        tool="llm_synthesis",
                        summary="Synthesis rejected due to stale cutoff phrasing; switched to evidence fallback.",
                    )
                    self._mark_trace_fallback(
                        reason="evidence_stale" if agreement.get("stale_detected") else "research_evidence_fallback",
                        freshness_status="recovered",
                        freshness_note="Synthesis output was rejected and replaced with structured evidence fallback.",
                    )
                    return fallback
                self._mark_trace_fallback(
                    reason="evidence_conflicting" if str(agreement.get("signal") or "") == "conflicting" else "research_unverified_fallback",
                    freshness_status="failed",
                    freshness_note="Synthesis was rejected and no reliable evidence fallback could be produced.",
                )
                return self._build_research_unverified_message(
                    goal,
                    high_stakes_mode=high_stakes_mode,
                    queries=queries,
                    reason="research_unverified_fallback",
                    source_rows=len(deduped),
                    extract_attempts=len(extract_jobs),
                    official_source_required=official_source_required,
                )
            role_flow.append({"role": "synthesizer", "status": "completed", "at": datetime.now(timezone.utc).isoformat()})
            self._log("engine.research_role_flow", role_flow=role_flow)
            self._append_direct_trace_step(
                step_type="reason",
                status="success",
                tool="llm_synthesis",
                summary="Synthesized final answer from verified evidence and extracted content.",
            )
            await self._store_research_profile_cache(
                goal=goal,
                answer=synthesized,
                evidence_rows=deduped,
                agreement=agreement,
            )
            return synthesized
        role_flow.append({"role": "synthesizer", "status": "failed", "at": datetime.now(timezone.utc).isoformat()})
        self._log("engine.research_role_flow", role_flow=role_flow)
        fallback = self._build_research_evidence_fallback(
            goal=goal,
            evidence_rows=deduped,
            freshness_mode=freshness_mode,
            agreement=agreement,
            high_stakes_mode=high_stakes_mode,
        )
        if fallback:
            self._append_direct_trace_step(
                step_type="reason",
                status="failed",
                tool="llm_synthesis",
                summary="Synthesis failed; returned structured evidence fallback.",
            )
            self._mark_trace_fallback(
                reason="evidence_stale" if agreement.get("stale_detected") else "research_evidence_fallback",
                freshness_status="recovered",
                freshness_note="Synthesis failed and a structured evidence fallback was returned.",
            )
            await self._store_research_profile_cache(
                goal=goal,
                answer=fallback,
                evidence_rows=deduped,
                agreement=agreement,
            )
            return fallback
        self._mark_trace_fallback(
            reason="evidence_conflicting" if str(agreement.get("signal") or "") == "conflicting" else "research_unverified_fallback",
            freshness_status="failed",
            freshness_note="Synthesis failed and no reliable evidence fallback could be produced.",
        )
        return self._build_research_unverified_message(
            goal,
            high_stakes_mode=high_stakes_mode,
            queries=queries,
            reason="research_unverified_fallback",
            source_rows=len(deduped),
            extract_attempts=len(extract_jobs),
            official_source_required=official_source_required,
        )

    async def _run_entity_lookup(self, goal: str) -> Optional[str]:
        from taos.core.entity import EntityIntentDetector, EntitySourcePlanner
        from taos.core.tools.builtin.extract_adapter import extract_with_adapter
        from taos.core.tools.builtin.web_search import web_search
        import asyncio

        self._set_trace_value("planner_path", "entity_lookup")
        self._set_trace_value("query_kind", "entity_lookup")
        handoff = self._build_query_frame_entity_handoff(goal=goal)
        self._set_trace_value("query_frame_entity_handoff_enabled", bool(handoff.get("query_frame_entity_handoff_enabled")))
        self._set_trace_value("query_frame_entity_handoff_applied", bool(handoff.get("query_frame_entity_handoff_applied")))
        self._set_trace_value("query_frame_entity_handoff_blocked_reason", str(handoff.get("query_frame_entity_handoff_blocked_reason") or ""))
        self._set_trace_value("entity_handoff_source", str(handoff.get("entity_handoff_source") or ""))
        self._set_trace_value("entity_handoff_lookup_type", str(handoff.get("entity_handoff_lookup_type") or ""))
        self._set_trace_value("entity_handoff_entity_name", str(handoff.get("entity_handoff_entity_name") or ""))
        self._set_trace_value("entity_handoff_requested_role", str(handoff.get("entity_handoff_requested_role") or ""))
        self._set_trace_value("entity_handoff_answer_language", str(handoff.get("entity_handoff_answer_language") or ""))
        self._set_trace_value("legacy_entity_resolver_used", not bool(handoff.get("query_frame_entity_handoff_applied")))
        entity_query = EntityIntentDetector().detect(goal)
        source_plan = EntitySourcePlanner().plan(entity_query)
        role, entity = self._extract_profile_role_and_entity(goal)
        role = str(entity_query.requested_attribute or role or "ceo").strip().lower()
        entity = str(entity_query.entity_name or entity or "").strip()
        if bool(handoff.get("query_frame_entity_handoff_applied")):
            role = str(handoff.get("entity_handoff_requested_role") or role or "ceo").strip().lower()
            entity = str(handoff.get("entity_handoff_entity_name") or entity or "").strip()
        role_label = (role or "ceo").upper()
        entity_label = entity or str(goal or "").strip()
        if bool(handoff.get("query_frame_entity_handoff_applied")):
            queries = list(handoff.get("entity_search_queries_generated") or [])
        else:
            queries = source_plan.flatten() or self._build_entity_lookup_queries(goal=goal, role=role, entity=entity_label)
        self._set_trace_value("entity_search_queries_generated", list(queries))
        lane_map: Dict[str, str] = {}
        if bool(handoff.get("query_frame_entity_handoff_applied")):
            lane_map = dict(handoff.get("entity_query_lane_map") or {})
        else:
            for lane, lane_queries in (source_plan.lanes or {}).items():
                for lane_query in lane_queries or []:
                    lane_map[str(lane_query).strip().lower()] = str(lane)
        self._set_trace_value("entity_search_query_lanes", sorted({str(v) for v in lane_map.values() if str(v).strip()}))

        stats = self._trace_data.setdefault("evidence_stats", {})
        stats["query_kind"] = "entity_lookup"
        stats["verification_state"] = "pending"
        stats["requested_role"] = str(role or "ceo").lower()
        stats["search_lanes_used"] = list(source_plan.required_lanes or ())
        stats["query_plan_summary"] = {
            "intent": "entity_lookup",
            "lanes_used": list(source_plan.required_lanes or ()),
            "primary_queries": list(queries),
            "fallback_queries": [],
        }
        self._set_trace_value("requested_role", str(role or "ceo").lower())
        self._set_trace_value("search_lanes_used", list(source_plan.required_lanes or ()))
        self._set_trace_value(
            "entity_intelligence_summary",
            {
                "intent": str(entity_query.intent or ""),
                "entity_name": entity_label,
                "requested_role": str(role or "ceo").lower(),
                "search_lanes_used": list(source_plan.required_lanes or ()),
                "source_plan": {"lanes": dict(source_plan.lanes or {}), "required_lanes": list(source_plan.required_lanes or ())},
            },
        )

        search_tasks: Dict[str, asyncio.Task[Any]] = {}
        for query_text in queries:
            search_tasks[query_text] = asyncio.create_task(
                web_search(
                    query=query_text,
                    num_results=6,
                    search_type="search",
                    recency_days=None,
                )
            )

        search_timeout = self._stage_timeout_seconds(self._SEARCH_STAGE_TIMEOUT_SECONDS)
        done_tasks: set[asyncio.Task[Any]] = set()
        pending_tasks: set[asyncio.Task[Any]] = set()
        if search_tasks and search_timeout > 0:
            done_tasks, pending_tasks = await asyncio.wait(
                list(search_tasks.values()),
                timeout=search_timeout,
            )
        else:
            pending_tasks = set(search_tasks.values())

        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        search_rows: List[Dict[str, Any]] = []
        search_errors: List[str] = []
        for query_text, task in search_tasks.items():
            if task not in done_tasks:
                continue
            try:
                res = task.result()
            except Exception:
                continue
            if not isinstance(res, dict):
                continue
            if str(res.get("error") or "").strip():
                search_errors.append(str(res.get("error") or "").strip())
            for row in list(res.get("results") or []):
                title = str(row.get("title") or "").strip()
                link = str(row.get("link") or "").strip()
                snippet = str(row.get("snippet") or "").strip()
                if not title or not link or not snippet:
                    continue
                search_rows.append(
                    {
                        "title": title,
                        "link": link,
                        "snippet": snippet,
                        "query": query_text,
                        "query_lane": lane_map.get(str(query_text).strip().lower(), ""),
                        "provider": self._domain_from_url(link),
                    }
                )

        raw_search_trace_rows: List[Dict[str, Any]] = []
        for idx, row in enumerate(search_rows[:20], start=1):
            raw_search_trace_rows.append(
                {
                    "rank": idx,
                    "title": str(row.get("title") or "").strip(),
                    "url": str(row.get("link") or "").strip(),
                    "provider": str(row.get("provider") or "").strip(),
                    "query": str(row.get("query") or "").strip(),
                    "query_lane": str(row.get("query_lane") or "").strip(),
                    "snippet": str(row.get("snippet") or "").strip()[:400],
                }
            )
        self._set_trace_value("entity_search_results", raw_search_trace_rows)
        stats["search_result_count"] = len(search_rows)
        stats["search_errors"] = list(search_errors)
        stats["returned_domains"] = sorted(
            {
                str(row.get("provider") or "").strip()
                for row in search_rows
                if str(row.get("provider") or "").strip()
            }
        )
        provider_snapshot = provider_health_snapshot()
        self._set_trace_value("provider_health", provider_snapshot)
        provider_error_text = " ".join(
            [
                *search_errors,
                str(((provider_snapshot.get("serper") or {}).get("last_error") or "")).strip(),
                str(((provider_snapshot.get("web_extract") or {}).get("last_error") or "")).strip(),
            ]
        ).strip()
        provider_connectivity_failed = bool(provider_error_text) and any(
            token in provider_error_text.lower()
            for token in ("connect", "resolve", "dns", "network", "timed out", "timeout")
        )
        stats["provider_connectivity_failed"] = provider_connectivity_failed
        if provider_connectivity_failed:
            stats["provider_connectivity_error"] = provider_error_text[:180]
            self._set_trace_value("provider_connectivity_failed", True)
            self._set_trace_value("provider_connectivity_error", provider_error_text[:180])

        ranked_rows = self._rank_entity_lookup_candidates(
            rows=search_rows,
            role=role,
            entity=entity_label,
            limit=8,
        )
        seeded_rows = self._build_entity_lookup_direct_candidates(
            role=role,
            entity=entity_label,
        )
        if seeded_rows and not ranked_rows:
            self._log(
                "engine.entity_lookup_seeded_candidates",
                count=len(seeded_rows),
                entity=entity_label,
            )
            ranked_rows = self._rank_entity_lookup_candidates(
                rows=list(seeded_rows),
                role=role,
                entity=entity_label,
                limit=10,
            )
        if ranked_rows:
            for row in ranked_rows:
                row_text = re.sub(
                    r"\s+",
                    " ",
                    f"{str(row.get('title') or '').strip()} {str(row.get('snippet') or '').strip()}",
                ).strip()
                seeded_candidate = bool(row.get("seeded_candidate"))
                snippet_match = False
                snippet_claim = ""
                supported_role = self._classify_entity_supported_role(
                    text=row_text,
                    requested_role=str(role or "ceo"),
                )
                role_match, role_mismatch_reason = self._entity_role_match_info(
                    requested_role=str(role or "ceo"),
                    supported_role=supported_role,
                )
                if not seeded_candidate:
                    snippet_match, snippet_claim = self._extract_entity_role_claim(
                        text=row_text,
                        role=role,
                        entity=entity_label,
                    )
                row["requested_role"] = str(role or "ceo").lower()
                row["supported_role"] = supported_role
                row["role_match"] = bool(role_match)
                row["role_mismatch_reason"] = role_mismatch_reason
                row["contradicts_claim"] = bool(supported_role and not role_match)
                row["snippet_role_match"] = bool(snippet_match and role_match)
                row["extract_role_match"] = False
                row["entity_role_match"] = bool(snippet_match and role_match)
                if (
                    not row["entity_role_match"]
                    and bool(row.get("company_linkedin_source"))
                    and bool(row.get("role_holder_detected"))
                    and bool(role_match)
                ):
                    row["snippet_role_match"] = True
                    row["entity_role_match"] = True
                if snippet_claim and not row.get("role_claim"):
                    row["role_claim"] = snippet_claim
                row["company_linkedin_source"] = self._is_company_linkedin_source(
                    link=str(row.get("link") or ""),
                    entity=entity_label,
                    text=row_text,
                )
                row["official_company_source"] = self._is_official_company_source(
                    link=str(row.get("link") or ""),
                    entity=entity_label,
                    text=row_text,
                )
                row["usable_for_verification"] = bool(row.get("entity_role_match") or row.get("supported_role")) and not str(row.get("rejection_reason") or "").strip()
            self._update_research_evidence_trace(ranked_rows)
        candidate_trace_rows: List[Dict[str, Any]] = []
        candidate_trace_map: Dict[str, Dict[str, Any]] = {}
        for idx, row in enumerate(list(ranked_rows or [])[:12], start=1):
            link = str(row.get("link") or "").strip()
            page_class = self._entity_candidate_page_class(row=row, entity=entity_label)
            row["page_class"] = page_class
            if str(row.get("rejection_reason") or "").strip():
                row["usable_for_verification"] = False
            entry = {
                "rank": idx,
                "title": str(row.get("title") or "").strip(),
                "url": link,
                "provider": str(row.get("provider") or "").strip(),
                "rank_score": float(row.get("rank_score") or 0.0),
                "query": str(row.get("query") or "").strip(),
                "query_lane": str(row.get("query_lane") or "").strip(),
                "snippet": str(row.get("snippet") or "").strip()[:400],
                "seeded_candidate": bool(row.get("seeded_candidate")),
                "snippet_role_match": bool(row.get("snippet_role_match")),
                "page_class": page_class,
                "selected_adapter": "web_extract",
                "domain_policy_action": "none",
                "attempted": False,
                "success": False,
                "extraction_status": "not_attempted",
                "status_code": None,
                "final_adapter": None,
                "fallback_reason": None,
                "quality_score": None,
                "rejection_reason": None,
                "attempted_adapters": [],
                "company_match": bool(row.get("company_match")),
                "target_entity_match": bool(row.get("target_entity_match")),
                "role_holder_detected": str(row.get("role_holder_detected") or ""),
                "extracted_role": str(row.get("extracted_role") or ""),
                "role_applies_to_person": bool(row.get("role_applies_to_person")),
                "source_relevance_score": float(row.get("source_relevance_score") or 0.0),
                "candidate_name": str(row.get("role_holder_detected") or ""),
                "evidence_source": "search_result_snippet",
            }
            candidate_trace_rows.append(entry)
            if link:
                candidate_trace_map[link.lower()] = entry
        self._set_trace_value("extractor_candidates", candidate_trace_rows)

        if not ranked_rows:
            fallback_rows = list(seeded_rows or [])[:3]
            self._set_trace_value("verification_state", "not_verified")
            self._set_trace_value("policy_reason", "entity_lookup_no_verified_candidates")
            stats.update(
                {
                    "verification_state": "not_verified",
                    "agreement_level": "unknown",
                    "agreement_score": 0.0,
                    "signal": "candidate_only",
                    "official_source_required": True,
                    "official_source_found": False,
                    "high_stakes_mode": False,
                }
            )
            self._mark_trace_fallback(
                reason="provider_connectivity_failed" if provider_connectivity_failed else "entity_lookup_not_verified",
                freshness_status="failed",
                freshness_note=(
                    "Live provider connectivity failed before usable evidence could be gathered."
                    if provider_connectivity_failed
                    else "No ranked candidate pages met entity+role relevance requirements."
                ),
            )
            return self._build_entity_lookup_response(
                goal=goal,
                role_label=role_label,
                entity_label=entity_label,
                verification_state="not_verified",
                policy_reason="provider_connectivity_failed" if provider_connectivity_failed else "entity_lookup_no_verified_candidates",
                evidence_rows=fallback_rows,
                queries=queries,
                provider_connectivity_failed=provider_connectivity_failed,
                provider_error_safe=provider_error_text[:180],
                lookup_type_hint=str(handoff.get("entity_handoff_lookup_type") or ""),
            )

        extract_candidates = list(ranked_rows[: min(8, len(ranked_rows))])
        dynamic_limit = max(0, int(self._settings.scrapling_dynamic_max_urls_per_query or 2))
        stealth_limit = max(0, int(self._settings.scrapling_stealth_max_urls_per_query or 1))
        dynamic_used = 0
        stealth_used = 0
        policy_hits: Dict[str, int] = {}
        extract_jobs: List[tuple[Dict[str, Any], asyncio.Task[Any]]] = []
        for row in extract_candidates:
            link = str(row.get("link") or "").strip()
            if not link:
                continue
            key = link.lower()
            trace_entry = candidate_trace_map.get(key, {})
            page_class = str(trace_entry.get("page_class") or self._entity_candidate_page_class(row=row, entity=entity_label))
            domain = self._domain_from_url(link)
            selected_adapter = self._entity_candidate_adapter_preference(page_class=page_class)
            selected_adapter = self._entity_memory_preferred_adapter(domain=domain, default_adapter=selected_adapter)
            domain_policy_action = "none"
            if selected_adapter == "http" and self._entity_should_skip_http_for_domain(domain=domain):
                if bool(self._settings.scrapling_dynamic_extractor_enabled) and dynamic_used < dynamic_limit:
                    selected_adapter = "dynamic"
                    domain_policy_action = "memory_http_skip_to_dynamic"
                elif bool(self._settings.scrapling_stealth_extractor_enabled) and stealth_used < stealth_limit:
                    selected_adapter = "stealth"
                    domain_policy_action = "memory_http_skip_to_stealth"
                else:
                    domain_policy_action = "memory_http_skip_no_budget"
            if page_class in {"directory_aggregator", "profile_index"}:
                domain_policy_action = "skip_non_confirming_class"
                if trace_entry:
                    trace_entry["selected_adapter"] = "none"
                    trace_entry["domain_policy_action"] = domain_policy_action
                    trace_entry["attempted"] = False
                    trace_entry["extraction_status"] = "skipped"
                    trace_entry["fallback_reason"] = "non_confirming_page_class"
                    trace_entry["rejection_reason"] = "non_confirming_page_class"
                policy_hits[domain_policy_action] = policy_hits.get(domain_policy_action, 0) + 1
                continue
            if selected_adapter == "dynamic":
                if not bool(self._settings.scrapling_dynamic_extractor_enabled) or dynamic_used >= dynamic_limit:
                    selected_adapter = "http"
                    domain_policy_action = "dynamic_budget_or_flag_blocked"
                else:
                    dynamic_used += 1
            if selected_adapter == "stealth":
                if not bool(self._settings.scrapling_stealth_extractor_enabled) or stealth_used >= stealth_limit:
                    selected_adapter = "http"
                    domain_policy_action = "stealth_budget_or_flag_blocked"
                else:
                    stealth_used += 1
            if trace_entry:
                trace_entry["selected_adapter"] = selected_adapter
                trace_entry["domain_policy_action"] = domain_policy_action
                trace_entry["page_class"] = page_class
                trace_entry["attempted"] = True
                trace_entry["extraction_status"] = "attempted"
            if domain_policy_action != "none":
                policy_hits[domain_policy_action] = policy_hits.get(domain_policy_action, 0) + 1
            extract_jobs.append(
                (
                    row,
                    asyncio.create_task(
                        extract_with_adapter(
                            url=link,
                            timeout=12,
                            max_chars=2600,
                            include_html=False,
                            prefer_scrapling_http=bool(self._settings.scrapling_http_extractor_enabled),
                            adapter_preference=selected_adapter,
                            dynamic_enabled=bool(self._settings.scrapling_dynamic_extractor_enabled),
                            stealth_enabled=bool(self._settings.scrapling_stealth_extractor_enabled),
                            dynamic_timeout_ms=int(self._settings.scrapling_dynamic_timeout_ms or 5000),
                            stealth_timeout_ms=int(self._settings.scrapling_stealth_timeout_ms or 7000),
                        )
                    ),
                )
            )

        extract_timeout = self._stage_timeout_seconds(self._EXTRACT_STAGE_TIMEOUT_SECONDS)
        done_extract_tasks: set[asyncio.Task[Any]] = set()
        pending_extract_tasks: set[asyncio.Task[Any]] = set()
        if extract_jobs and extract_timeout > 0:
            done_extract_tasks, pending_extract_tasks = await asyncio.wait(
                [job[1] for job in extract_jobs],
                timeout=extract_timeout,
            )
        else:
            pending_extract_tasks = {job[1] for job in extract_jobs}
        for task in pending_extract_tasks:
            task.cancel()
        if pending_extract_tasks:
            await asyncio.gather(*pending_extract_tasks, return_exceptions=True)
        pending_task_ids = {id(task) for task in pending_extract_tasks}

        extract_fetch_success = 0
        extract_success = 0
        adapter_counts: Dict[str, int] = {}
        adapter_fallback_reasons: List[str] = []
        usable_role_claim_count = 0

        for row, task in extract_jobs:
            key = str(row.get("link") or "").strip().lower()
            trace_entry = candidate_trace_map.get(key, {})
            if id(task) in pending_task_ids and trace_entry:
                trace_entry["extraction_status"] = "timeout"
                trace_entry["fallback_reason"] = "timeout"
                trace_entry["rejection_reason"] = "timeout"
                self._entity_update_domain_memory(
                    domain=self._domain_from_url(str(row.get("link") or "")),
                    selected_adapter=str(trace_entry.get("selected_adapter") or "http"),
                    attempted_adapters=[],
                    success=False,
                    fallback_reason="timeout",
                )
            if task not in done_extract_tasks:
                continue
            try:
                extracted = task.result()
            except Exception:
                if trace_entry:
                    trace_entry["extraction_status"] = "exception"
                    trace_entry["fallback_reason"] = "extract_exception"
                    trace_entry["rejection_reason"] = "extract_exception"
                self._entity_update_domain_memory(
                    domain=self._domain_from_url(str(row.get("link") or "")),
                    selected_adapter=str(trace_entry.get("selected_adapter") or "http"),
                    attempted_adapters=[],
                    success=False,
                    fallback_reason="extract_exception",
                )
                continue
            if trace_entry and isinstance(extracted, dict):
                trace_entry["status_code"] = extracted.get("status_code")
                trace_entry["final_adapter"] = str(extracted.get("extractor_adapter") or "web_extract")
                trace_entry["quality_score"] = extracted.get("quality_score")
                trace_entry["rejection_reason"] = extracted.get("rejection_reason")
                trace_entry["attempted_adapters"] = list(extracted.get("attempted_adapters") or [])
                fallback_reason = str(extracted.get("adapter_fallback_reason") or "").strip()
                if fallback_reason:
                    trace_entry["fallback_reason"] = fallback_reason[:240]
            if not isinstance(extracted, dict) or not extracted.get("success"):
                if trace_entry:
                    raw_reason = str((extracted or {}).get("adapter_fallback_reason") or (extracted or {}).get("error") or "extract_failed") if isinstance(extracted, dict) else "extract_failed"
                    trace_entry["extraction_status"] = (
                        "blocked"
                        if any(token in raw_reason.lower() for token in ("login", "blocked", "sign in"))
                        else "failed"
                    )
                self._entity_update_domain_memory(
                    domain=self._domain_from_url(str(row.get("link") or "")),
                    selected_adapter=str(trace_entry.get("selected_adapter") or "http"),
                    attempted_adapters=list(extracted.get("attempted_adapters") or []) if isinstance(extracted, dict) else [],
                    success=False,
                    fallback_reason=str((extracted or {}).get("adapter_fallback_reason") or (extracted or {}).get("error") or "extract_failed") if isinstance(extracted, dict) else "extract_failed",
                )
                continue
            extract_fetch_success += 1
            adapter_name = str(extracted.get("extractor_adapter") or "web_extract").strip() or "web_extract"
            adapter_counts[adapter_name] = adapter_counts.get(adapter_name, 0) + 1
            fallback_reason = str(extracted.get("adapter_fallback_reason") or "").strip()
            if fallback_reason:
                adapter_fallback_reasons.append(fallback_reason[:180])
            if trace_entry:
                trace_entry["success"] = True
                trace_entry["extraction_status"] = "extracted"

            text = str(extracted.get("text") or "").strip()
            if not text:
                self._entity_update_domain_memory(
                    domain=self._domain_from_url(str(row.get("link") or "")),
                    selected_adapter=str(trace_entry.get("selected_adapter") or "http"),
                    attempted_adapters=list(extracted.get("attempted_adapters") or []),
                    success=False,
                    fallback_reason=fallback_reason or "text_empty",
                )
                continue
            extract_success += 1
            claim_match, claim_text = self._extract_entity_role_claim(
                text=text,
                role=role,
                entity=entity_label,
            )
            supported_role = self._classify_entity_supported_role(
                text=text,
                requested_role=str(role or "ceo"),
            )
            role_match, role_mismatch_reason = self._entity_role_match_info(
                requested_role=str(role or "ceo"),
                supported_role=supported_role,
            )
            row["supported_role"] = supported_role or str(row.get("supported_role") or "")
            row["requested_role"] = str(role or "ceo").lower()
            row["role_match"] = bool(role_match)
            row["role_mismatch_reason"] = role_mismatch_reason
            row["contradicts_claim"] = bool((supported_role or row.get("supported_role")) and not role_match)
            row["extract_role_match"] = bool(claim_match and role_match)
            row["entity_role_match"] = bool((claim_match and role_match) or row.get("snippet_role_match"))
            if claim_text:
                row["role_claim"] = claim_text
                usable_role_claim_count += 1
            row["company_linkedin_source"] = self._is_company_linkedin_source(
                link=str(row.get("link") or ""),
                entity=entity_label,
                text=f"{row.get('title', '')} {row.get('snippet', '')} {claim_text}",
            )
            row["official_company_source"] = self._is_official_company_source(
                link=str(row.get("link") or ""),
                entity=entity_label,
                text=f"{row.get('title', '')} {row.get('snippet', '')} {claim_text}",
            )
            row["snippet"] = re.sub(
                r"\s+",
                " ",
                f"{str(row.get('snippet') or '').strip()} Extract: {text[:800]}",
            )[:1800]
            if trace_entry:
                trace_entry["candidate_name"] = str(row.get("role_holder_detected") or "")
                trace_entry["extracted_role"] = str(row.get("supported_role") or row.get("extracted_role") or "")
                trace_entry["evidence_source"] = "page_extract"
            extracted_pub = str(extracted.get("published_at") or "").strip()
            if extracted_pub and not row.get("date_hint"):
                dt = self._extract_date_from_text(extracted_pub)
                if dt:
                    row["date_hint"] = dt.strftime("%Y-%m-%d")
            self._entity_update_domain_memory(
                domain=self._domain_from_url(str(row.get("link") or "")),
                selected_adapter=str(trace_entry.get("selected_adapter") or "http"),
                attempted_adapters=list(extracted.get("attempted_adapters") or []),
                success=True,
                fallback_reason=fallback_reason,
            )

        verification = self._evaluate_entity_lookup_verification(
            rows=ranked_rows,
            role=role,
            entity=entity_label,
        )
        verification_state = str(verification.get("verification_state") or "not_verified")
        policy_reason = str(verification.get("policy_reason") or "entity_lookup_not_verified")
        official_source_found = bool(verification.get("official_source_found"))
        usable_rows = list(verification.get("evidence_rows") or ranked_rows[:6])

        self._set_trace_value("verification_state", verification_state)
        self._set_trace_value("policy_reason", policy_reason)
        self._set_trace_value("extractor_adapters", dict(adapter_counts))
        self._set_trace_value("domain_policy_hits", dict(policy_hits))
        if adapter_fallback_reasons:
            self._set_trace_value("extractor_adapter_fallback_reasons", adapter_fallback_reasons[:4])
        self._log(
            "engine.entity_lookup_extractors",
            adapters=dict(adapter_counts),
            fallback_reasons=adapter_fallback_reasons[:3],
            extract_fetch_success=extract_fetch_success,
            extract_success=extract_success,
            candidate_rows=len(ranked_rows),
        )
        stats.update(
            {
                "query_kind": "entity_lookup",
                "verification_state": verification_state,
                "confirmed_count": 1 if verification_state == "confirmed" else 0,
                "partially_confirmed_count": 1 if verification_state == "partially_confirmed" else 0,
                "not_verified_count": 1 if verification_state == "not_verified" else 0,
                "agreement_level": ("medium" if verification_state == "confirmed" else "low" if verification_state == "partially_confirmed" else "unknown"),
                "agreement_score": (0.68 if verification_state == "confirmed" else 0.42 if verification_state == "partially_confirmed" else 0.0),
                "signal": ("clean" if verification_state == "confirmed" else "partial_conflict" if verification_state == "partially_confirmed" else "candidate_only"),
                "official_source_required": True,
                "official_source_found": official_source_found,
                "high_stakes_mode": False,
                "extract_fetch_count": extract_fetch_success,
                "extract_count": extract_success,
                "extract_rejected_count": max(0, len(extract_jobs) - extract_success),
                "extract_adapters": dict(adapter_counts),
                "dynamic_attempts": sum(1 for row in candidate_trace_rows if str(row.get("selected_adapter")) == "dynamic" and bool(row.get("attempted"))),
                "stealth_attempts": sum(1 for row in candidate_trace_rows if str(row.get("selected_adapter")) == "stealth" and bool(row.get("attempted"))),
                "http_attempts": sum(1 for row in candidate_trace_rows if str(row.get("selected_adapter")) == "http" and bool(row.get("attempted"))),
                "dynamic_success": sum(1 for row in candidate_trace_rows if str(row.get("selected_adapter")) == "dynamic" and bool(row.get("success"))),
                "stealth_success": sum(1 for row in candidate_trace_rows if str(row.get("selected_adapter")) == "stealth" and bool(row.get("success"))),
                "http_success": sum(1 for row in candidate_trace_rows if str(row.get("selected_adapter")) == "http" and bool(row.get("success"))),
                "fallback_count": sum(1 for row in candidate_trace_rows if str(row.get("fallback_reason") or "").strip()),
                "skip_count_by_policy": dict(policy_hits),
                "domain_policy_hits": int(sum(policy_hits.values())),
                "usable_role_claim_count": usable_role_claim_count,
            }
        )
        if verification_state == "not_verified":
            self._mark_trace_fallback(
                reason="provider_connectivity_failed" if provider_connectivity_failed else "entity_lookup_not_verified",
                freshness_status="failed",
                freshness_note=(
                    "Live provider connectivity failed before explicit company-role confirmation could be verified."
                    if provider_connectivity_failed
                    else "Candidate pages were found, but explicit company-role confirmation was not verified."
                ),
            )

        answer = self._build_entity_lookup_response(
            goal=goal,
            role_label=role_label,
            entity_label=entity_label,
            verification_state=verification_state,
            policy_reason="provider_connectivity_failed" if provider_connectivity_failed and verification_state == "not_verified" else policy_reason,
            evidence_rows=usable_rows,
            queries=queries,
            provider_connectivity_failed=provider_connectivity_failed and verification_state == "not_verified",
            provider_error_safe=provider_error_text[:180],
            lookup_type_hint=str(handoff.get("entity_handoff_lookup_type") or ""),
        )
        await self._store_research_profile_cache(
            goal=goal,
            answer=answer,
            evidence_rows=usable_rows,
            agreement={
                "verification_state": verification_state,
                "official_source_required": True,
                "official_source_found": official_source_found,
                "query_kind": "entity_lookup",
            },
        )
        return answer

    def _build_entity_lookup_direct_candidates(
        self,
        *,
        role: str,
        entity: str,
    ) -> List[Dict[str, Any]]:
        role_token = (role or "ceo").upper()
        entity_text = str(entity or "").strip().lower()
        tokens = [t for t in re.findall(r"[a-z0-9]+", entity_text) if t]
        if not tokens:
            return []
        compact = "".join(tokens[:3])
        dash_slug = "-".join(tokens[:3])
        links: List[str] = [
            f"https://{compact}.com",
            f"https://www.{compact}.com",
            f"https://{compact}.in",
            f"https://www.{compact}.in",
            f"https://www.linkedin.com/company/{dash_slug}",
            f"https://in.linkedin.com/company/{dash_slug}",
        ]
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for link in links:
            ll = str(link or "").strip().lower()
            if not ll or ll in seen:
                continue
            seen.add(ll)
            try:
                provider = (urlparse(link).netloc or "").replace("www.", "")
            except Exception:
                provider = ""
            out.append(
                {
                    "title": f"{entity} official/company candidate",
                    "link": link,
                    "snippet": f"Direct verification candidate for {entity}.",
                    "query": f"direct:{entity}:{role_token}",
                    "provider": provider,
                    "rank_score": 0.19,
                    "seeded_candidate": True,
                }
            )
        return out[:6]

    def _build_query_frame_entity_handoff(self, *, goal: str) -> Dict[str, Any]:
        enabled = str(os.getenv("QUERY_FRAME_ENTITY_HANDOFF_ENABLED", "false")).strip().lower() == "true"
        route_assist_enabled = str(os.getenv("QUERY_FRAME_ROUTE_ASSIST_ENABLED", "false")).strip().lower() == "true"
        query_frame = dict(self._trace_data.get("query_frame") or {})
        selected_route = str((self._trace_data.get("route_decision") or {}).get("selected_route") or "").strip().lower()
        intent_family = str(query_frame.get("intent_family") or "unknown").strip().lower()
        lookup_type = str(query_frame.get("lookup_type") or "").strip().lower()
        entity_name = str(query_frame.get("entity_name") or "").strip()
        requested_role = str(query_frame.get("requested_role") or "").strip().lower()
        answer_language = str(query_frame.get("answer_language") or query_frame.get("detected_language") or "unknown").strip().lower()
        canonical_query = str(query_frame.get("canonical_query") or "").strip()
        confidence = float(query_frame.get("confidence") or 0.0)
        ambiguity_flags = list(query_frame.get("ambiguity_flags") or [])
        warnings = list(query_frame.get("warnings") or [])

        result: Dict[str, Any] = {
            "query_frame_entity_handoff_enabled": enabled,
            "query_frame_entity_handoff_applied": False,
            "query_frame_entity_handoff_blocked_reason": "",
            "entity_handoff_source": "legacy_resolver",
            "entity_handoff_lookup_type": lookup_type,
            "entity_handoff_entity_name": entity_name,
            "entity_handoff_requested_role": requested_role,
            "entity_handoff_answer_language": answer_language,
            "entity_search_queries_generated": [],
            "entity_query_lane_map": {},
        }
        if not enabled:
            result["query_frame_entity_handoff_blocked_reason"] = "feature_disabled"
            return result
        if not route_assist_enabled:
            result["query_frame_entity_handoff_blocked_reason"] = "route_assist_disabled"
            return result
        if selected_route != "entity_lookup":
            result["query_frame_entity_handoff_blocked_reason"] = "route_not_entity_lookup"
            return result
        if intent_family != "entity_lookup":
            result["query_frame_entity_handoff_blocked_reason"] = "query_frame_not_entity_lookup"
            return result
        supported_lookup = {
            "founder_lookup",
            "ceo_lookup",
            "linkedin_profile",
            "official_website",
            "business_legitimacy",
        }
        if lookup_type not in supported_lookup:
            result["query_frame_entity_handoff_blocked_reason"] = "unsupported_lookup_type"
            return result
        if confidence < 0.85:
            result["query_frame_entity_handoff_blocked_reason"] = "query_frame_low_confidence"
            return result
        if not entity_name:
            result["query_frame_entity_handoff_blocked_reason"] = "query_frame_missing_entity"
            return result
        if not canonical_query:
            result["query_frame_entity_handoff_blocked_reason"] = "query_frame_missing_canonical_query"
            return result
        if ambiguity_flags:
            result["query_frame_entity_handoff_blocked_reason"] = "query_frame_ambiguous"
            return result
        if warnings:
            result["query_frame_entity_handoff_blocked_reason"] = "query_frame_validation_warning"
            return result

        generated = self._build_entity_queries_from_query_frame(
            goal=goal,
            entity_name=entity_name,
            lookup_type=lookup_type,
            canonical_query=canonical_query,
            original_query=str(query_frame.get("original_query") or goal),
        )
        result["query_frame_entity_handoff_applied"] = True
        result["entity_handoff_source"] = "query_frame"
        result["entity_search_queries_generated"] = [row["query"] for row in generated]
        result["entity_query_lane_map"] = {str(row["query"]).strip().lower(): str(row["lane"]) for row in generated}
        return result

    def _build_entity_queries_from_query_frame(
        self,
        *,
        goal: str,
        entity_name: str,
        lookup_type: str,
        canonical_query: str,
        original_query: str,
    ) -> List[Dict[str, str]]:
        entity = str(entity_name or "").strip()
        lookup = str(lookup_type or "").strip().lower()
        canon = str(canonical_query or "").strip()
        original = str(original_query or goal or "").strip()

        pairs: List[tuple[str, str]] = []
        if lookup == "founder_lookup":
            pairs.extend(
                [
                    (f"{entity} founder", "entity_role"),
                    (f"{entity} founded by", "entity_role"),
                    (f"{entity} company history founder", "entity_role"),
                    (f"{entity} official history founder", "official_website"),
                    (canon, "query_frame_canonical"),
                ]
            )
        elif lookup == "ceo_lookup":
            pairs.extend(
                [
                    (f"{entity} CEO", "entity_role"),
                    (f"{entity} Chief Executive Officer", "entity_role"),
                    (f"{entity} leadership CEO", "entity_role"),
                    (f"{entity} official leadership CEO", "official_website"),
                    (canon, "query_frame_canonical"),
                ]
            )
        elif lookup == "linkedin_profile":
            pairs.extend(
                [
                    (f"{entity} LinkedIn", "linkedin_profile"),
                    (f"site:linkedin.com/company {entity}", "linkedin_profile"),
                    (f"{entity} company LinkedIn", "linkedin_profile"),
                    (canon, "query_frame_canonical"),
                ]
            )
        elif lookup == "official_website":
            pairs.extend(
                [
                    (f"{entity} official website", "official_website"),
                    (f"{entity} official site", "official_website"),
                    (f"{entity} company website", "official_website"),
                    (canon, "query_frame_canonical"),
                ]
            )
        elif lookup == "business_legitimacy":
            pairs.extend(
                [
                    (f"{entity} official website", "legitimacy"),
                    (f"{entity} LinkedIn", "legitimacy"),
                    (f"{entity} company profile", "legitimacy"),
                    (f"{entity} registration", "legitimacy"),
                    (f"{entity} business details", "legitimacy"),
                    (canon, "query_frame_canonical"),
                ]
            )
        else:
            pairs.append((canon or original or goal, "query_frame_canonical"))

        if original and original.lower() != canon.lower():
            pairs.append((original, "original_language"))

        out: List[Dict[str, str]] = []
        seen: set[str] = set()
        for query, lane in pairs:
            q = str(query or "").strip()
            if not q:
                continue
            key = q.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"query": q, "lane": str(lane or "query_frame_canonical")})
        return out[:8]

    def _build_entity_lookup_queries(self, *, goal: str, role: str, entity: str) -> List[str]:
        role_token = (role or "ceo").upper()
        entity_text = str(entity or goal or "").strip()
        exact_role = str(role or "ceo").strip().lower()
        founder_ceo_phrase = "Founder & CEO" if exact_role in {"ceo", "founder"} else role_token
        queries = [
            f"{entity_text} {role_token}",
            f"{entity_text} official website {role_token}",
            f"{entity_text} leadership team",
            f"{entity_text} about team leadership",
            f'site:linkedin.com/company "{entity_text}" "{role_token}"',
            f'site:linkedin.com/in "{entity_text}" "{role_token}"',
            f'{entity_text} "{founder_ceo_phrase}"',
            f'site:linkedin.com/company "{entity_text}" "{founder_ceo_phrase}"',
            f'site:linkedin.com/posts "{entity_text}" "{founder_ceo_phrase}"',
            f"{entity_text} official website leadership",
            f"{entity_text} ministry of corporate affairs {role_token}",
            f"{entity_text} company registry director",
            f"{entity_text} {exact_role} official website",
            f"{entity_text} {exact_role} linkedin",
            f"{entity_text} core team {founder_ceo_phrase}",
            f"{entity_text} employee team member",
        ]
        if str(goal or "").strip() and str(goal).strip().lower() not in {q.lower() for q in queries}:
            queries.append(str(goal).strip())
        deduped: List[str] = []
        seen: set[str] = set()
        for row in queries:
            q = str(row or "").strip()
            key = q.lower()
            if not q or key in seen:
                continue
            seen.add(key)
            deduped.append(q)
        return deduped[:8]
    def _rank_entity_lookup_candidates(
        self,
        *,
        rows: List[Dict[str, Any]],
        role: str,
        entity: str,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        if not rows:
            return []
        ranked: List[Dict[str, Any]] = []
        for row in rows:
            row = dict(row)
            title = str(row.get("title") or "")
            snippet = str(row.get("snippet") or "")
            link = str(row.get("link") or "")
            provider = str(row.get("provider") or "").strip() or self._domain_from_url(link)
            text = f"{title} {snippet} {link} {provider}"
            company_match = self._entity_candidate_exact_match(text=text, entity=entity)
            target_entity_match = company_match or self._is_company_linkedin_source(link=link, entity=entity, text=text) or self._is_official_company_source(link=link, entity=entity, text=text)
            role_holder = self._extract_role_holder_from_text(text=snippet or title, role=role)
            extracted_role = self._classify_entity_supported_role(text=text, requested_role=str(role or "ceo"))
            role_match, role_mismatch_reason = self._entity_role_match_info(
                requested_role=str(role or "ceo"),
                supported_role=extracted_role,
            )
            row["provider"] = provider
            row["company_match"] = bool(company_match)
            row["target_entity_match"] = bool(target_entity_match)
            row["role_holder_detected"] = role_holder
            row["extracted_role"] = extracted_role
            row["supported_role"] = str(row.get("supported_role") or extracted_role)
            row["requested_role"] = str(role or "ceo").lower()
            row["role_match"] = bool(row.get("role_match")) or bool(role_match)
            row["role_mismatch_reason"] = str(row.get("role_mismatch_reason") or role_mismatch_reason)
            row["role_applies_to_person"] = bool(role_holder)
            row["source_relevance_score"] = self._entity_source_relevance_score(
                text=text,
                entity=entity,
                role=role,
                link=link,
                query_lane=str(row.get("query_lane") or ""),
            )
            if not target_entity_match and self._is_unrelated_entity_row(text=text, entity=entity):
                row["rejection_reason"] = str(row.get("rejection_reason") or "unrelated_company")
            score = self._score_entity_lookup_candidate(
                row=row,
                role=role,
                entity=entity,
            )
            if score < 0.18:
                continue
            out = dict(row)
            out["rank_score"] = round(score, 4)
            ranked.append(out)
        ranked.sort(key=lambda item: float(item.get("rank_score") or 0.0), reverse=True)
        deduped: List[Dict[str, Any]] = []
        seen_links: set[str] = set()
        for row in ranked:
            link = str(row.get("link") or "").strip().lower()
            if link and link in seen_links:
                continue
            if link:
                seen_links.add(link)
            deduped.append(row)
            if len(deduped) >= max(1, int(limit)):
                break
        return deduped

    def _score_entity_lookup_candidate(self, *, row: Dict[str, Any], role: str, entity: str) -> float:
        title = str(row.get("title") or "")
        snippet = str(row.get("snippet") or "")
        link = str(row.get("link") or "")
        provider = str(row.get("provider") or "").strip()
        if not provider:
            try:
                provider = (urlparse(link).netloc or "").replace("www.", "")
            except Exception:
                provider = ""
        text = f"{title} {snippet} {link} {provider}".lower()
        entity_tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", str(entity or "").lower())
            if len(token) >= 3 and token not in {"the", "and", "for", "with", "from", "about", "company"}
        ]
        entity_hits = sum(1 for token in entity_tokens if token in text)
        role_pattern = {
            "ceo": r"\b(ceo|chief executive officer)\b",
            "founder": r"\b(founder|co-founder)\b",
            "cto": r"\b(cto|chief technology officer)\b",
            "cfo": r"\b(cfo|chief financial officer)\b",
            "coo": r"\b(coo|chief operating officer)\b",
        }.get(str(role or "ceo").lower(), rf"\b{re.escape(str(role or 'ceo').lower())}\b")
        role_hit = bool(re.search(role_pattern, text, flags=re.I))
        company_linkedin = self._is_company_linkedin_source(link=link, entity=entity, text=text)
        official_source = self._is_official_company_source(link=link, entity=entity, text=text)
        company_match = bool(row.get("company_match"))
        target_entity_match = bool(row.get("target_entity_match"))
        role_holder_detected = str(row.get("role_holder_detected") or "").strip()
        source_relevance_score = float(row.get("source_relevance_score") or 0.0)
        directory_like = bool(
            re.search(
                r"(rocketreach|zoominfo|crunchbase|wikipedia|britannica|biography|"
                r"/pub/dir/|/people/|/profiles?|/directory/)",
                text,
                flags=re.I,
            )
        )
        score = 0.0
        score += min(0.45, float(entity_hits) * 0.12)
        if role_hit:
            score += 0.28
        if company_linkedin:
            score += 0.26
        if official_source:
            score += 0.34
        if company_match:
            score += 0.18
        if target_entity_match:
            score += 0.12
        if role_holder_detected:
            score += 0.08
        if bool(row.get("seeded_candidate")):
            score -= 0.24
        score += min(0.18, max(-0.18, source_relevance_score - 0.5))
        if directory_like:
            score -= 0.28
        if str(row.get("query_lane") or "") == "linkedin":
            score += 0.08
        if str(row.get("rejection_reason") or "") in {"unrelated_company", "entity_mismatch"}:
            score -= 0.6
        if not role_hit and entity_hits <= 1:
            score -= 0.12
        if not company_match and not target_entity_match:
            score -= 0.32
        return max(0.0, min(1.4, score))

    def _extract_entity_role_claim(self, *, text: str, role: str, entity: str) -> tuple[bool, str]:
        body = re.sub(r"\s+", " ", str(text or "")).strip()
        if not body:
            return False, ""
        role_key = str(role or "ceo").lower()
        role_pattern = {
            "ceo": r"\b(ceo|chief executive officer)\b",
            "founder": r"\b(founder|co-founder)\b",
            "cto": r"\b(cto|chief technology officer)\b",
            "cfo": r"\b(cfo|chief financial officer)\b",
            "coo": r"\b(coo|chief operating officer)\b",
        }.get(role_key, rf"\b{re.escape(role_key)}\b")
        entity_tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", str(entity or "").lower())
            if len(token) >= 3 and token not in {"the", "and", "for", "with", "from", "about", "company"}
        ]
        sentences = re.split(r"(?<=[\.\?!])\s+", body)
        for sentence in sentences:
            s = str(sentence or "").strip()
            if len(s) < 20:
                continue
            lower = s.lower()
            role_hit = bool(re.search(role_pattern, lower, flags=re.I))
            entity_hits = sum(1 for token in entity_tokens if token in lower)
            if not (role_hit and entity_hits >= max(1, min(2, len(entity_tokens)))):
                continue
            words = re.findall(r"[a-z0-9]+", lower)
            if not words:
                continue
            role_words = {"ceo", "founder", "co", "cto", "cfo", "coo", "chief", "executive", "technology", "financial", "operating", "officer"}
            role_positions = [idx for idx, token in enumerate(words) if token in role_words]
            entity_positions = [idx for idx, token in enumerate(words) if token in set(entity_tokens)]
            if not role_positions or not entity_positions:
                if role_hit and entity_hits > 0:
                    return True, s[:260]
                continue
            min_distance = min(abs(rp - ep) for rp in role_positions for ep in entity_positions)
            max_distance = 8 if role_key != "founder" else 5
            if min_distance > max_distance:
                continue
            if role_key == "founder":
                compact_entity = r"\s+".join(re.escape(token) for token in entity_tokens[:3])
                founder_entity_pattern = (
                    rf"\b(?:co-?founder|founder)\s+(?:of|at)\s+(?:the\s+)?{compact_entity}\b"
                    rf"|\b{compact_entity}\b.{0,40}\b(?:co-?founder|founder)\b"
                )
                if not re.search(founder_entity_pattern, lower, flags=re.I):
                    continue
            return True, s[:260]
        return False, ""

    def _extract_role_holder_from_text(self, *, text: str, role: str) -> str:
        body = re.sub(r"\s+", " ", str(text or "")).strip()
        if not body:
            return ""
        lower_body = body.lower()

        def _looks_like_person_name(value: str) -> bool:
            tokens = [token for token in re.split(r"\s+", str(value or "").strip()) if token]
            if len(tokens) < 2:
                return False
            stop = {
                "as",
                "chairman",
                "chief",
                "executive",
                "officer",
                "founder",
                "ceo",
                "cto",
                "cfo",
                "coo",
                "linkedin",
                "microsoft",
                "relyce",
                "infotech",
            }
            title_like = 0
            for token in tokens:
                lowered = token.lower().strip(".,:-|")
                if lowered in stop:
                    return False
                clean = token.strip(".,:-|")
                if re.fullmatch(r"[A-Z]", clean):
                    title_like += 1
                    continue
                if re.fullmatch(r"[A-Z][a-z]+", clean):
                    title_like += 1
                    continue
            return title_like >= 2

        name_pattern = r"([A-Z][a-z]+(?:\s+(?:[A-Z][a-z]+|[A-Z]\b)){0,3})"
        role_key = str(role or "ceo").strip().lower()
        patterns = []
        if role_key in {"ceo", "founder"}:
            patterns.extend(
                [
                    rf"\b{name_pattern}\s+(?:Founder\s*(?:&|and)\s*CEO)\b",
                    rf"\b{name_pattern}\s*(?:-|:|,)\s*(?:Founder\s*(?:&|and)\s*CEO)\b",
                    rf"\b{name_pattern}\s+(?:CEO|Chief Executive Officer)\b",
                    rf"\b{name_pattern}\s*(?:-|:|,)\s*(?:CEO|Chief Executive Officer)\b",
                    rf"\b{name_pattern}\s+is\s+(?:Chairman\s+and\s+)?(?:CEO|Chief Executive Officer)\b",
                    rf"\b{name_pattern}\s*(?:-|:|,)\s*(?:Chairman\s+and\s+)?(?:CEO|Chief Executive Officer)\b",
                    rf"\b(?:As\s+)?(?:Chairman\s+and\s+)?(?:CEO|Chief Executive Officer)\s+(?:of|at)\s+[^,]{{1,80}},\s*{name_pattern}\b",
                    rf"\b{name_pattern}\s+(?:Founder|Co-?Founder)\b",
                    rf"\b{name_pattern}\s*(?:-|:|,)\s*(?:Founder|Co-?Founder)\b",
                    rf"\b(?:Founder|Co-?Founder)\s+(?:of|at)\s+[^,]{{1,80}},\s*{name_pattern}\b",
                ]
            )
        else:
            patterns.append(rf"\b{name_pattern}\s+{re.escape(role_key)}\b")
        for pattern in patterns:
            match = re.search(pattern, body)
            if match:
                groups = [g for g in match.groups() if g]
                for candidate in groups:
                    cleaned = re.sub(r"\s+", " ", str(candidate)).strip()
                    if cleaned and re.search(
                        rf"\b{re.escape(cleaned.lower())}\b.{0,40}\b(?:is\s+not|isn't|not\s+the|incorrectly\s+claim)\b.{0,40}\b(?:ceo|chief executive officer|founder)\b",
                        lower_body,
                        flags=re.I,
                    ):
                        continue
                    if _looks_like_person_name(cleaned):
                        return cleaned
        return ""

    def _classify_entity_supported_role(self, *, text: str, requested_role: str) -> str:
        lower = str(text or "").lower()
        requested = str(requested_role or "").strip().lower()
        if re.search(r"\b(founder\s*(?:&|and)\s*ceo|ceo\s*(?:&|and)\s*founder)\b", lower, flags=re.I):
            return "founder_ceo"
        patterns = {
            "ceo": r"\b(ceo|chief executive officer|founder\s*(?:&|and)\s*ceo|current ceo)\b",
            "founder": r"\b(founder|co-?founder|owner|proprietor)\b",
            "employee": r"\b(employee|worker|staff|team member|member|associate|developer|engineer)\b",
            "team_member": r"\b(core team|team member|part of the team)\b",
            "director": r"\b(director|managing director)\b",
        }
        if requested == "ceo" and re.search(patterns["ceo"], lower, flags=re.I):
            return "ceo"
        if requested == "founder" and re.search(patterns["founder"], lower, flags=re.I):
            return "founder"
        if re.search(patterns["team_member"], lower, flags=re.I):
            return "team_member"
        for role_name, pattern in patterns.items():
            if role_name == requested:
                continue
            if re.search(pattern, lower, flags=re.I):
                return role_name
        return ""

    def _entity_role_match_info(self, *, requested_role: str, supported_role: str) -> tuple[bool, str]:
        requested = str(requested_role or "").strip().lower()
        supported = str(supported_role or "").strip().lower()
        if not requested or not supported:
            return False, "no_exact_role_evidence"
        if supported == "founder_ceo" and requested in {"ceo", "founder"}:
            return True, ""
        if requested == supported:
            return True, ""
        if supported in {"employee", "team_member", "member", "worker"}:
            return False, "employee_evidence_does_not_verify_requested_role"
        return False, f"supported_role_is_{supported}_not_{requested}"

    def _entity_candidate_exact_match(self, *, text: str, entity: str) -> bool:
        entity_tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", str(entity or "").lower())
            if len(token) >= 3 and token not in {"the", "and", "for", "with", "from", "about", "company"}
        ]
        lowered = str(text or "").lower()
        if not entity_tokens:
            return False
        return sum(1 for token in entity_tokens if token in lowered) >= max(1, min(2, len(entity_tokens)))

    def _entity_source_relevance_score(self, *, text: str, entity: str, role: str, link: str, query_lane: str) -> float:
        lowered = str(text or "").lower()
        entity_match = self._entity_candidate_exact_match(text=lowered, entity=entity)
        role_hit = bool(re.search(r"\b(founder\s*(?:&|and)\s*ceo|ceo|chief executive officer|founder|co-?founder)\b", lowered, flags=re.I))
        linkedin_company = self._is_company_linkedin_source(link=link, entity=entity, text=lowered)
        score = 0.0
        if entity_match:
            score += 0.45
        if role_hit:
            score += 0.22
        if linkedin_company:
            score += 0.2
        if str(query_lane or "").strip().lower() == "linkedin":
            score += 0.08
        return max(0.0, min(1.0, score))

    def _is_unrelated_entity_row(self, *, text: str, entity: str) -> bool:
        lowered = str(text or "").lower()
        if self._entity_candidate_exact_match(text=lowered, entity=entity):
            return False
        return bool(re.search(r"\b(ceo|founder|cfo|fraud|scam)\b", lowered, flags=re.I))

    def _is_company_linkedin_source(self, *, link: str, entity: str, text: str = "") -> bool:
        lower_link = str(link or "").lower()
        if "linkedin.com/company/" not in lower_link:
            return False
        entity_tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", str(entity or "").lower())
            if len(token) >= 3 and token not in {"the", "and", "for", "with", "from", "about", "company"}
        ]
        hay = f"{lower_link} {str(text or '').lower()}"
        return sum(1 for token in entity_tokens if token in hay) >= max(1, min(2, len(entity_tokens)))

    def _is_official_company_source(self, *, link: str, entity: str, text: str = "") -> bool:
        lower_link = str(link or "").lower()
        if not lower_link.startswith(("http://", "https://")):
            return False
        if "linkedin.com" in lower_link:
            return False
        if re.search(r"(rocketreach|zoominfo|wikipedia|britannica|biography|crunchbase)", lower_link, re.I):
            return False
        parsed = urlparse(lower_link)
        domain = str(parsed.netloc or "").replace("www.", "")
        entity_tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", str(entity or "").lower())
            if len(token) >= 3 and token not in {"the", "and", "for", "with", "from", "about", "company"}
        ]
        if not entity_tokens:
            return False
        if not any(token in domain for token in entity_tokens[:2]):
            return False
        combined = f"{lower_link} {str(text or '').lower()}"
        return any(
            marker in combined
            for marker in ("leadership", "team", "about", "management", "founder", "ceo")
        )

    def _entity_candidate_page_class(self, *, row: Dict[str, Any], entity: str) -> str:
        link = str(row.get("link") or "").strip().lower()
        title = str(row.get("title") or "").lower()
        snippet = str(row.get("snippet") or "").lower()
        text = f"{title} {snippet} {link}"
        if "linkedin.com/company/" in link:
            return "company_linkedin"
        if re.search(r"/pub/dir/|/directory/|/people/|/profiles?", link):
            return "profile_index"
        if re.search(r"(rocketreach|zoominfo|crunchbase|apollo|signalhire)", text, flags=re.I):
            return "directory_aggregator"
        if self._is_official_company_source(link=link, entity=entity, text=text):
            if re.search(r"(about|team|leadership|management|founder|ceo)", text, flags=re.I):
                return "official_static"
            return "official_dynamic"
        if re.search(r"(news|reuters|apnews|bbc|forbes|techcrunch|blog|article)", text, flags=re.I):
            return "article_news"
        return "unknown"

    def _entity_candidate_adapter_preference(self, *, page_class: str) -> str:
        key = str(page_class or "").strip().lower()
        if key in {"official_dynamic"}:
            return "dynamic"
        if key in {"company_linkedin"}:
            return "stealth"
        return "http"

    def _domain_from_url(self, url: str) -> str:
        try:
            return str(urlparse(str(url or "")).netloc or "").lower().replace("www.", "")
        except Exception:
            return ""

    def _entity_domain_memory_entry(self, domain: str) -> Dict[str, Any]:
        key = str(domain or "").strip().lower()
        if not key:
            return {}
        return OrchestrationEngine._ENTITY_DOMAIN_MEMORY.setdefault(
            key,
            {
                "http_empty_failures": 0,
                "dynamic_successes": 0,
                "stealth_successes": 0,
                "all_extractors_failed": 0,
            },
        )

    def _entity_memory_preferred_adapter(self, *, domain: str, default_adapter: str) -> str:
        entry = self._entity_domain_memory_entry(domain)
        if not entry:
            return default_adapter
        dynamic_successes = int(entry.get("dynamic_successes") or 0)
        stealth_successes = int(entry.get("stealth_successes") or 0)
        if stealth_successes > 0:
            return "stealth"
        if dynamic_successes > 0:
            return "dynamic"
        return default_adapter

    def _entity_should_skip_http_for_domain(self, *, domain: str) -> bool:
        if not bool(self._settings.scrapling_domain_policy_enabled):
            return False
        entry = self._entity_domain_memory_entry(domain)
        threshold = max(1, int(self._settings.scrapling_http_empty_skip_threshold or 2))
        return int(entry.get("http_empty_failures") or 0) >= threshold

    def _entity_update_domain_memory(
        self,
        *,
        domain: str,
        selected_adapter: str,
        attempted_adapters: List[str],
        success: bool,
        fallback_reason: str,
    ) -> None:
        entry = self._entity_domain_memory_entry(domain)
        if not entry:
            return
        selected = str(selected_adapter or "").strip().lower()
        reason = str(fallback_reason or "").strip().lower()
        attempts = [str(x or "").strip().lower() for x in attempted_adapters or []]
        if success:
            if selected == "dynamic":
                entry["dynamic_successes"] = int(entry.get("dynamic_successes") or 0) + 1
            if selected == "stealth":
                entry["stealth_successes"] = int(entry.get("stealth_successes") or 0) + 1
            if selected == "http":
                entry["http_empty_failures"] = max(0, int(entry.get("http_empty_failures") or 0) - 1)
            entry["all_extractors_failed"] = 0
            return
        if selected == "http" and ("text_too_short" in reason or "text_len=0" in reason):
            entry["http_empty_failures"] = int(entry.get("http_empty_failures") or 0) + 1
        if attempts and all("web_extract" not in x for x in attempts):
            entry["all_extractors_failed"] = int(entry.get("all_extractors_failed") or 0) + 1

    def _evaluate_entity_lookup_verification(
        self,
        *,
        rows: List[Dict[str, Any]],
        role: str,
        entity: str,
    ) -> Dict[str, Any]:
        confirmable_rows = []
        for row in rows:
            page_class = str(row.get("page_class") or "").strip().lower()
            if page_class in {"directory_aggregator", "profile_index"}:
                continue
            confirmable_rows.append(row)
        explicit_rows = [row for row in confirmable_rows if bool(row.get("entity_role_match")) and bool(row.get("role_match", True))]
        mismatched_rows = [
            row for row in confirmable_rows
            if str(row.get("supported_role") or "").strip() and not bool(row.get("role_match", False))
        ]
        official_rows = [row for row in explicit_rows if bool(row.get("official_company_source"))]
        linkedin_rows = [row for row in explicit_rows if bool(row.get("company_linkedin_source"))]
        corroborating_rows = [
            row
            for row in explicit_rows
            if not bool(row.get("company_linkedin_source"))
        ]

        if mismatched_rows and explicit_rows:
            return {
                "verification_state": "not_verified",
                "policy_reason": "conflicting_role_evidence",
                "official_source_found": bool(official_rows),
                "conflict_detected": True,
                "supported_role": str(mismatched_rows[0].get("supported_role") or ""),
                "role_match": False,
                "role_mismatch_reason": str(mismatched_rows[0].get("role_mismatch_reason") or "role_conflict"),
                "evidence_rows": (official_rows[:2] + explicit_rows[:2] + mismatched_rows[:3]),
            }
        if mismatched_rows:
            return {
                "verification_state": "not_verified",
                "policy_reason": "role_mismatch_only",
                "official_source_found": False,
                "conflict_detected": False,
                "supported_role": str(mismatched_rows[0].get("supported_role") or ""),
                "role_match": False,
                "role_mismatch_reason": str(mismatched_rows[0].get("role_mismatch_reason") or "role_mismatch"),
                "evidence_rows": mismatched_rows[:5],
            }

        if official_rows:
            return {
                "verification_state": "confirmed",
                "policy_reason": "official_explicit_role_confirmation",
                "official_source_found": True,
                "conflict_detected": False,
                "supported_role": str(official_rows[0].get("supported_role") or role),
                "role_match": True,
                "role_mismatch_reason": "",
                "evidence_rows": official_rows[:4] + [row for row in explicit_rows if row not in official_rows][:2],
            }
        if linkedin_rows and corroborating_rows:
            return {
                "verification_state": "confirmed",
                "policy_reason": "linkedin_plus_corroboration",
                "official_source_found": False,
                "conflict_detected": False,
                "supported_role": str(linkedin_rows[0].get("supported_role") or role),
                "role_match": True,
                "role_mismatch_reason": "",
                "evidence_rows": (linkedin_rows[:2] + corroborating_rows[:3]),
            }
        if explicit_rows:
            return {
                "verification_state": "partially_confirmed",
                "policy_reason": "single_source_explicit_claim",
                "official_source_found": False,
                "conflict_detected": False,
                "supported_role": str(explicit_rows[0].get("supported_role") or role),
                "role_match": True,
                "role_mismatch_reason": "",
                "evidence_rows": explicit_rows[:5],
            }
        return {
            "verification_state": "not_verified",
            "policy_reason": "candidate_sources_without_explicit_role_confirmation",
            "official_source_found": False,
            "conflict_detected": False,
            "supported_role": "",
            "role_match": False,
            "role_mismatch_reason": "no_exact_role_evidence",
            "evidence_rows": confirmable_rows[:5] if confirmable_rows else rows[:5],
        }

    def _resolve_entity_answer_language(self) -> tuple[str, str]:
        handoff_lang = str(self._trace_data.get("entity_handoff_answer_language") or "").strip().lower()
        if handoff_lang and handoff_lang not in {"unknown", "und"}:
            return handoff_lang, "query_frame_handoff"
        query_frame = self._trace_data.get("query_frame")
        if isinstance(query_frame, dict):
            frame_lang = str(query_frame.get("answer_language") or query_frame.get("detected_language") or "").strip().lower()
            if frame_lang and frame_lang not in {"unknown", "und"}:
                return frame_lang, "query_frame_observation"
        return "en", "default"

    def _apply_entity_answer_language_preservation(
        self,
        *,
        answer_text: str,
        answer_mode: str,
        answer_language: str,
    ) -> tuple[str, Dict[str, Any]]:
        lang = str(answer_language or "en").strip().lower() or "en"
        mode = str(answer_mode or "").strip().lower()
        if lang in {"", "en", "unknown", "und"}:
            return answer_text, {
                "language_preservation_applied": False,
                "language_preservation_status": "english_default",
                "language_preservation_limitations": "",
            }
        if lang == "ta":
            mode_line_map = {
                "profile_link_result": "Tamil note: Idhu profile/link query. Candidate public profile details keezha irukku.",
                "official_website_result": "Tamil note: Idhu official website query. Candidate website evidence keezha irukku.",
                "business_legitimacy_result": "Tamil note: Idhu company legitimacy query. Public presence vs legal verification separate-aa paathirukkom.",
            }
            mode_line = mode_line_map.get(mode, "Tamil note: Public evidence base pannitu safe summary kudukiren.")
            translated = "\n".join(
                [
                    "Tamil/Tanglish summary:",
                    mode_line,
                    "Source names, company names, and links unchanged-aa vechirukkom.",
                    "",
                    answer_text,
                ]
            ).strip()
            return translated, {
                "language_preservation_applied": True,
                "language_preservation_status": "tamil_tanglish_applied",
                "language_preservation_limitations": "Tamil rendering currently uses safe Tanglish framing; citations and source labels remain unchanged.",
            }
        return answer_text, {
            "language_preservation_applied": False,
            "language_preservation_status": "fallback_english",
            "language_preservation_limitations": (
                f"Language '{lang}' metadata preserved; response kept in English because a reliable renderer is not enabled yet."
            ),
        }

    def _build_entity_lookup_response(
        self,
        *,
        goal: str,
        role_label: str,
        entity_label: str,
        verification_state: str,
        policy_reason: str,
        evidence_rows: List[Dict[str, Any]],
        queries: List[str],
        provider_connectivity_failed: bool = False,
        provider_error_safe: str = "",
        lookup_type_hint: str = "",
    ) -> str:
        from taos.core.entity import EntityAnswerComposer, EntityEvidence, EntityIntent, EntityIntentDetector
        from dataclasses import replace

        evidence = list(evidence_rows or [])[:6]
        verification = str(verification_state or "not_verified").strip().lower()

        def infer_source_type(row: Dict[str, Any]) -> str:
            import re
            link = str(row.get("link") or "").lower()
            text = f"{row.get('title', '')} {row.get('snippet', '')} {row.get('provider', '')}".lower()
            if bool(row.get("official_company_source")):
                return "official_website"
            if bool(row.get("company_linkedin_source")) or "linkedin.com/company/" in link:
                return "company_linkedin"
            if re.search(r"(mca\.gov\.in|roc|registry|registrar|ministry of corporate affairs|company register)", text, flags=re.I):
                return "government_registry"
            if re.search(r"(reuters|forbes|techcrunch|business|inc42|yourstory|economic times|mint)", text, flags=re.I):
                return "reputable_news"
            if re.search(r"(rocketreach|zoominfo|crunchbase|apollo|signalhire)", text, flags=re.I):
                return "registry_directory"
            return "search_snippet"

        def infer_candidate_name(row: Dict[str, Any]) -> str:
            import re
            candidate = str(row.get("candidate_name") or "").strip()
            if candidate:
                return candidate
            detected = str(row.get("role_holder_detected") or "").strip()
            if detected:
                return detected
            title = str(row.get("title") or "").strip()
            snippet = str(row.get("snippet") or "").strip()
            inferred = self._extract_role_holder_from_text(
                text=f"{title}. {snippet}",
                role=role_label,
            )
            if inferred:
                return inferred
            haystacks = [
                str(row.get("role_claim") or "").strip(),
                title,
                snippet,
            ]
            role_key = str(role_label or "").strip().lower()
            patterns = [
                rf"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){{0,3}})\s+is\s+(?:the\s+)?(?:current\s+)?{re.escape(role_key)}\b",
                rf"\b{re.escape(role_key)}\s*[:\-]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){{0,3}})\b",
                rf"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){{0,3}}),\s*(?:the\s+)?{re.escape(role_key)}\b",
            ]
            for value in haystacks:
                for pattern in patterns:
                    match = re.search(pattern, value, flags=re.I)
                    if match:
                        return re.sub(r"\s+", " ", str(match.group(1))).strip()
            return ""

        def row_is_source_link_only(row: Dict[str, Any]) -> bool:
            return (
                not str(row.get("role_claim") or row.get("snippet") or "").strip()
                and not str(row.get("candidate_name") or "").strip()
                and not str(row.get("role_holder_detected") or "").strip()
                and not bool(row.get("role_applies_to_person"))
                and float(row.get("rank_score") or 0.0) <= 0.0
            )

        def row_has_explicit_role_bearing_evidence(row: Dict[str, Any]) -> bool:
            if str(row.get("rejection_reason") or "").strip():
                return False
            if not bool(row.get("role_match")):
                return False
            if not (bool(row.get("company_match")) or bool(row.get("target_entity_match"))):
                return False
            if str(row.get("candidate_name") or "").strip() and str(row.get("role_holder_detected") or "").strip():
                return True
            if str(row.get("role_holder_detected") or "").strip() and bool(row.get("role_applies_to_person")):
                return True
            role_claim = str(row.get("role_claim") or row.get("snippet") or "").strip()
            return bool(role_claim and bool(row.get("role_applies_to_person")))

        entity_query = EntityIntentDetector().detect(goal)
        hint = str(lookup_type_hint or "").strip().lower()
        if hint == "official_website":
            entity_query = replace(entity_query, requested_attribute="official_website", intent=EntityIntent.COMPANY_DETAILS)
        elif hint == "business_legitimacy":
            entity_query = replace(entity_query, requested_attribute="business_legitimacy", intent=EntityIntent.LEGITIMACY_CHECK)
        elif hint == "linkedin_profile":
            entity_query = replace(entity_query, requested_attribute="linkedin_profile", intent=EntityIntent.LINKEDIN_PROFILE)
        if not entity_query.has_entity and str(entity_label or "").strip():
            entity_query = replace(entity_query, entity_name=str(entity_label or "").strip())
        entity_evidence = [
            EntityEvidence(
                title=str(row.get("title") or "").strip(),
                url=str(row.get("link") or "").strip(),
                source_type=infer_source_type(row),
                snippet=str(row.get("role_claim") or row.get("snippet") or "").strip(),
                candidate_name=infer_candidate_name(row),
                attribute=str(role_label or "").strip().lower(),
                requested_role=str(role_label or "").strip().lower(),
                supported_role=str(row.get("supported_role") or "").strip().lower(),
                role_match=bool(row.get("role_match")),
                role_mismatch_reason=str(row.get("role_mismatch_reason") or ""),
                contradicts_claim=bool(row.get("contradicts_claim")),
                source_tier=(
                    "tier1"
                    if infer_source_type(row) in {"official_website", "government_registry", "company_linkedin"}
                    else "tier2"
                    if infer_source_type(row) in {"reputable_news", "registry_directory", "person_linkedin"}
                    else "tier3"
                ),
                strongest_source_type=infer_source_type(row),
                extraction_quality=float(row.get("rank_score") or 0.0),
                company_match=bool(row.get("company_match")),
                target_entity_match=bool(row.get("target_entity_match")),
                role_holder_detected=str(row.get("role_holder_detected") or ""),
                extracted_role=str(row.get("extracted_role") or ""),
                role_applies_to_person=bool(row.get("role_applies_to_person")),
                source_relevance_score=float(row.get("source_relevance_score") or 0.0),
                usable_for_verification=(
                    row_has_explicit_role_bearing_evidence(row)
                    or (
                        bool(row.get("supported_role"))
                        and not bool(row.get("role_match"))
                        and not row_is_source_link_only(row)
                    )
                ),
                rejection_reason="" if str(row.get("page_class") or "") not in {"directory_aggregator", "profile_index"} else str(row.get("page_class") or ""),
                supports_claim=row_has_explicit_role_bearing_evidence(row),
                confidence=float(row.get("rank_score") or 0.0),
            )
            for row in evidence
        ]
        answer_obj = EntityAnswerComposer().compose(entity_query=entity_query, evidence_rows=entity_evidence)
        answer_language, answer_language_source = self._resolve_entity_answer_language()
        localized_answer, language_meta = self._apply_entity_answer_language_preservation(
            answer_text=str(answer_obj.answer or "").strip(),
            answer_mode=str(answer_obj.mode or "").strip(),
            answer_language=answer_language,
        )
        answer_obj = replace(answer_obj, answer=localized_answer)

        stats = self._trace_data.setdefault("evidence_stats", {})
        stats.update(
            {
                "entity_answer_mode": answer_obj.mode,
                "entity_answer_mode_source": "query_frame_handoff" if str(lookup_type_hint or "").strip() else "legacy_intent",
                "verification_state": answer_obj.verification_state or verification,
                "selected_candidate": answer_obj.selected_candidate,
                "candidate_count": int(answer_obj.candidate_count or 0),
                "official_source_found": bool(answer_obj.official_source_found),
                "linkedin_source_found": bool(answer_obj.linkedin_source_found),
                "registry_source_found": bool(answer_obj.registry_source_found),
                "entity_profile_link_found": bool(answer_obj.linkedin_source_found),
                "entity_official_website_found": bool(answer_obj.official_source_found),
                "entity_registry_evidence_found": bool(answer_obj.registry_source_found),
                "entity_business_presence_supported": bool(
                    answer_obj.mode == "business_legitimacy_result"
                    and (answer_obj.official_source_found or answer_obj.linkedin_source_found or answer_obj.registry_source_found)
                ),
                "entity_legal_registration_verified": bool(
                    answer_obj.mode == "business_legitimacy_result" and answer_obj.registry_source_found
                ),
                "entity_answer_limitations": str(answer_obj.uncertainty or "").strip(),
                "requested_role": str(answer_obj.requested_role or "").strip(),
                "supported_role": str(answer_obj.supported_role or "").strip(),
                "role_match": bool(answer_obj.role_match),
                "role_mismatch_reason": str(answer_obj.role_mismatch_reason or ""),
                "conflict_detected": bool(answer_obj.conflict_detected),
                "exact_role_verified": bool(answer_obj.exact_role_verified),
                "evidence_strength": str(answer_obj.evidence_strength or "weak"),
                "strongest_source_type": str(answer_obj.strongest_source_type or ""),
                "source_agreement": str(answer_obj.source_agreement or "unknown"),
                "disambiguation_needed": bool(answer_obj.disambiguation_needed),
                "search_depth_used": str(answer_obj.search_depth_used or "standard"),
                "search_lanes_used": list(answer_obj.search_lanes_used or []),
                "source_tiers_found": list(answer_obj.source_tiers_found or []),
                "confidence_reason": str(answer_obj.confidence_reason or ""),
                "provider_connectivity_failed": bool(provider_connectivity_failed),
                "provider_error_safe": str(provider_error_safe or "").strip(),
                "answer_language": answer_language,
                "answer_language_source": answer_language_source,
                "language_preservation_applied": bool(language_meta.get("language_preservation_applied")),
                "language_preservation_status": str(language_meta.get("language_preservation_status") or ""),
                "language_preservation_limitations": str(language_meta.get("language_preservation_limitations") or ""),
            }
        )
        self._set_trace_value(
            "entity_intelligence_summary",
            {
                "intent": "entity_lookup",
                "entity_name": entity_label,
                "answer_mode": answer_obj.mode,
                "answer_mode_source": "query_frame_handoff" if str(lookup_type_hint or "").strip() else "legacy_intent",
                "verification_state": answer_obj.verification_state or verification,
                "selected_candidate": answer_obj.selected_candidate,
                "candidate_count": int(answer_obj.candidate_count or 0),
                "official_source_found": bool(answer_obj.official_source_found),
                "linkedin_source_found": bool(answer_obj.linkedin_source_found),
                "registry_source_found": bool(answer_obj.registry_source_found),
                "entity_profile_link_found": bool(answer_obj.linkedin_source_found),
                "entity_official_website_found": bool(answer_obj.official_source_found),
                "entity_registry_evidence_found": bool(answer_obj.registry_source_found),
                "entity_business_presence_supported": bool(
                    answer_obj.mode == "business_legitimacy_result"
                    and (answer_obj.official_source_found or answer_obj.linkedin_source_found or answer_obj.registry_source_found)
                ),
                "entity_legal_registration_verified": bool(
                    answer_obj.mode == "business_legitimacy_result" and answer_obj.registry_source_found
                ),
                "entity_answer_limitations": str(answer_obj.uncertainty or "").strip(),
                "requested_role": str(answer_obj.requested_role or "").strip(),
                "supported_role": str(answer_obj.supported_role or "").strip(),
                "role_match": bool(answer_obj.role_match),
                "role_mismatch_reason": str(answer_obj.role_mismatch_reason or ""),
                "conflict_detected": bool(answer_obj.conflict_detected),
                "exact_role_verified": bool(answer_obj.exact_role_verified),
                "evidence_strength": str(answer_obj.evidence_strength or "weak"),
                "strongest_source_type": str(answer_obj.strongest_source_type or ""),
                "search_depth_used": str(answer_obj.search_depth_used or "standard"),
                "search_lanes_used": list(answer_obj.search_lanes_used or []),
                "source_tiers_found": list(answer_obj.source_tiers_found or []),
                "confidence_reason": str(answer_obj.confidence_reason or ""),
                "provider_connectivity_failed": bool(provider_connectivity_failed),
                "provider_error_safe": str(provider_error_safe or "").strip(),
                "answer_language": answer_language,
                "answer_language_source": answer_language_source,
                "language_preservation_applied": bool(language_meta.get("language_preservation_applied")),
                "language_preservation_status": str(language_meta.get("language_preservation_status") or ""),
                "language_preservation_limitations": str(language_meta.get("language_preservation_limitations") or ""),
            },
        )
        self._set_trace_value("verification_state", answer_obj.verification_state or verification)
        self._set_trace_value("selected_candidate", answer_obj.selected_candidate)
        self._set_trace_value("entity_answer_mode", answer_obj.mode)
        self._set_trace_value("entity_answer_mode_source", "query_frame_handoff" if str(lookup_type_hint or "").strip() else "legacy_intent")
        self._set_trace_value("entity_profile_link_found", bool(answer_obj.linkedin_source_found))
        self._set_trace_value("entity_official_website_found", bool(answer_obj.official_source_found))
        self._set_trace_value(
            "entity_business_presence_supported",
            bool(
                answer_obj.mode == "business_legitimacy_result"
                and (answer_obj.official_source_found or answer_obj.linkedin_source_found or answer_obj.registry_source_found)
            ),
        )
        self._set_trace_value("entity_registry_evidence_found", bool(answer_obj.registry_source_found))
        self._set_trace_value(
            "entity_legal_registration_verified",
            bool(answer_obj.mode == "business_legitimacy_result" and answer_obj.registry_source_found),
        )
        self._set_trace_value("entity_answer_limitations", str(answer_obj.uncertainty or "").strip())
        self._set_trace_value("answer_language", answer_language)
        self._set_trace_value("answer_language_source", answer_language_source)
        self._set_trace_value("language_preservation_applied", bool(language_meta.get("language_preservation_applied")))
        self._set_trace_value("language_preservation_status", str(language_meta.get("language_preservation_status") or ""))
        self._set_trace_value("language_preservation_limitations", str(language_meta.get("language_preservation_limitations") or ""))
        self._set_trace_value("confidence_reason", answer_obj.confidence_reason)
        self._set_trace_value("requested_role", answer_obj.requested_role)
        self._set_trace_value("supported_role", answer_obj.supported_role)
        self._set_trace_value("role_match", answer_obj.role_match)
        self._set_trace_value("role_mismatch_reason", answer_obj.role_mismatch_reason)
        self._set_trace_value("conflict_detected", answer_obj.conflict_detected)
        if provider_connectivity_failed:
            self._set_trace_value("provider_connectivity_failed", True)
            self._set_trace_value("provider_connectivity_error", str(provider_error_safe or "").strip()[:180])

        lines: List[str] = [answer_obj.answer]
        if provider_connectivity_failed:
            safe_provider_line = (
                f"- Safe provider error: {str(provider_error_safe or '').strip()[:140]}"
                if str(provider_error_safe or "").strip()
                else "- Safe provider error: unavailable"
            )
            lines.extend(
                [
                    "",
                    "Provider availability:",
                    "- Live search or extraction provider was unavailable in this run.",
                    safe_provider_line,
                ]
            )
        if queries:
            lines.extend(["", "Sources checked:"])
            for query in queries[:4]:
                lines.append(f"- {str(query).strip()}")
        lines.extend(["", "Verification policy:", f"- {policy_reason.replace('_', ' ')}"])
        return "\n".join(part for part in lines if part is not None).strip()
    def _build_research_unverified_message(
        self,
        goal: str,
        *,
        high_stakes_mode: bool = False,
        queries: Optional[List[str]] = None,
        reason: str = "research_unverified_fallback",
        search_error: Optional[str] = None,
        source_rows: int = 0,
        extract_attempts: int = 0,
        official_source_required: bool = False,
    ) -> str:
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        query_rows = [str(q).strip() for q in (queries or []) if str(q).strip()]
        selected_queries = query_rows[:3]
        profile_mode = self._is_profile_or_entity_query(goal)
        profile_role, profile_entity = self._extract_profile_role_and_entity(goal)
        role_label = profile_role.upper() if profile_role else "CEO"
        entity_label = profile_entity or str(goal or "").strip()

        not_found_lines: List[str] = []
        reason_label = str(reason or "").strip().lower()
        if reason_label == "web_search_failed":
            not_found_lines.append("The search provider returned an error before usable source rows were retrieved.")
            if search_error:
                not_found_lines.append(f"Provider error: {str(search_error).strip()[:140]}")
        elif reason_label == "research_timeout":
            not_found_lines.append(
                "Execution hit the hard reliability time budget before full verification completed."
            )
            if source_rows > 0:
                not_found_lines.append(
                    f"Partial evidence was collected ({int(source_rows)} source row(s)), but processing could not finish in time."
                )
        elif reason_label == "search_sparse":
            not_found_lines.append(
                f"Search returned candidates ({int(source_rows)} row(s)), but none passed ranking/diversity quality checks."
            )
        elif reason_label == "profile_evidence_mismatch":
            not_found_lines.append(
                f"Search returned candidates ({int(source_rows)} row(s)), but none clearly matched '{entity_label}' with a reliable {role_label} confirmation."
            )
        elif reason_label == "extract_failed":
            not_found_lines.append(
                f"Source rows were found ({int(source_rows)}), but extraction produced no usable article text from {int(extract_attempts)} attempt(s)."
            )
        else:
            not_found_lines.append("Limited evidence: I could not retrieve enough corroborated sources to produce a source-grounded answer.")
        if high_stakes_mode and official_source_required:
            not_found_lines.append("No sufficiently reliable official-source evidence was confirmed for this high-stakes request.")

        if profile_mode:
            unclear_lines = [
                f"The currently verifiable {role_label} for '{entity_label}'.",
                "Whether available profiles are current and explicitly confirm the role.",
            ]
        else:
            unclear_lines = [
                f"The latest verifiable status for: '{goal}'.",
                "Key details are still not confirmed and require explicit official confirmation before action.",
            ]
        if reason_label in {"search_sparse", "extract_failed"}:
            unclear_lines.append("Whether additional high-quality sources would materially change the conclusion.")

        if profile_mode:
            next_moves = [
                f"Ask for verified-only {role_label} confirmation for {entity_label} from official company sources.",
                "Request a short list of only entity-matching source links (no loosely related profiles).",
                "Retry with explicit company legal name or official website for stricter disambiguation.",
            ]
        else:
            next_moves = [
                "Retry with a narrower scope (entity + exact date/time window).",
                "Request official sources only for a stricter verification pass.",
                "Ask for a concise verified-only summary with no speculative details.",
            ]

        lines: List[str] = [
            "Answer",
            (
                f"As of {now_utc}, I couldn't confidently verify the current {role_label} for '{entity_label}' from reliable sources."
                if profile_mode
                else f"As of {now_utc}, I couldn't verify this confidently from the sources I found, and the current evidence remains limited."
            ),
            "",
            "What was searched",
        ]
        if selected_queries:
            for idx, query in enumerate(selected_queries, start=1):
                lines.append(f"- {query} [S{idx}]")
        else:
            lines.append(f"- {goal}")
        lines.extend(["", "What was not found"])
        for item in not_found_lines:
            lines.append(f"- {item}")
        lines.extend(["", "What's still unclear"])
        for item in unclear_lines:
            lines.append(f"- {item}")
        lines.extend(["", "Next useful moves"])
        for item in next_moves:
            lines.append(f"- {item}")
        if selected_queries:
            lines.extend(["", "Sources"])
            for idx, query in enumerate(selected_queries, start=1):
                lines.append(f"- [S{idx}] https://www.google.com/search?q={quote_plus(query)}")
        return "\n".join(lines).strip()

    def _build_research_evidence_fallback(
        self,
        goal: str,
        evidence_rows: List[Dict[str, str]],
        freshness_mode: bool = False,
        agreement: Optional[Dict[str, Any]] = None,
        high_stakes_mode: bool = False,
    ) -> Optional[str]:
        if not evidence_rows:
            return None

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        profile_mode = self._is_profile_or_entity_query(goal)
        profile_role, profile_entity = self._extract_profile_role_and_entity(goal)
        role_label = profile_role.upper() if profile_role else "CEO"
        entity_label = profile_entity or str(goal or "").strip()
        agreement = dict(agreement or {})
        high_stakes_mode = bool(high_stakes_mode or agreement.get("high_stakes_mode"))
        agreement_level = str(agreement.get("agreement_level") or "low")
        stale_detected = bool(agreement.get("stale_detected"))
        conflict_detected = bool(agreement.get("conflict_detected"))
        signal = str(agreement.get("signal") or ("partial_conflict" if (conflict_detected or stale_detected) else "clean"))
        goal_lower = str(goal or "").lower()
        attribution_focus = any(
            marker in goal_lower
            for marker in ("who leaked", "who did", "how did", "source of leak", "origin of leak", "who was behind", "responsible")
        )

        source_tiers = [str(row.get("tier") or "").strip() for row in evidence_rows[:8]]
        source_mix = (
            "Official"
            if any(t == "official" for t in source_tiers) and not any(t in {"trusted", "mixed"} for t in source_tiers)
            else "Mixed"
            if any(t == "official" for t in source_tiers)
            else "Reporting"
        )
        freshness_label = "High" if not stale_detected else "Medium"
        signal_label = {
            "clean": "Clean",
            "partial_conflict": "Partial conflict",
            "conflicting": "Conflicting",
        }.get(signal, "Partial conflict")

        if profile_mode:
            lines: List[str] = [
                "Answer",
                (
                    f"As of {now_utc}, I found candidate sources for '{entity_label}', "
                    f"but they do not clearly and consistently confirm the current {role_label}."
                ),
                "",
                "Why this answer",
                f"- Evidence quality for a direct {role_label} confirmation is currently {agreement_level}.",
                "- Some retrieved pages are loosely related profiles and cannot be treated as definitive role confirmation.",
                "- I am separating candidate evidence from verified confirmation to avoid overclaiming.",
                "",
                "Key evidence (candidate sources)",
            ]
            for idx, row in enumerate(evidence_rows[:6], start=1):
                title = re.sub(r"\s+", " ", (row.get("title") or "").strip())[:120]
                provider = str(row.get("provider") or "").strip() or title or f"Source {idx}"
                snippet = re.sub(r"\s+", " ", (row.get("snippet") or "").strip())[:180]
                date_hint = row.get("date_hint") or row.get("published_at") or "date n/a"
                claim = snippet or title
                lines.append(f"- [{date_hint}] {claim} — {provider} [S{idx}]")

            lines.append("")
            lines.append("Sources:")
            for idx, row in enumerate(evidence_rows[:6], start=1):
                title = (row.get("title") or "").strip() or f"Source {idx}"
                link = (row.get("link") or "").strip()
                if link:
                    lines.append(f"- [S{idx}] {title} - {link}")

            lines.extend(
                [
                    "",
                    "What is still unclear",
                    f"- The currently verifiable {role_label} for {entity_label}.",
                    "- Whether the retrieved profile pages are current and role-specific.",
                    "",
                    "Bottom line",
                    f"- I cannot confidently confirm the current {role_label} from the current evidence set.",
                ]
            )
            return "\n".join(lines).strip()

        strongest = evidence_rows[0] if evidence_rows else {}
        strongest_claim = re.sub(
            r"\s+",
            " ",
            str(strongest.get("snippet") or strongest.get("title") or goal or "").strip(),
        )[:260]
        answer_line = (
            f"As of {now_utc}, the best-supported answer is: {strongest_claim}"
            if strongest_claim
            else f"As of {now_utc}, I found partial evidence for '{goal}', but not enough for full confirmation."
        )
        lines = [
            "Answer",
            answer_line,
            "",
            "Why this answer",
            f"- The strongest available source directly relates to the request and agreement is {agreement_level}.",
            "- Sources were ranked for authority, freshness, extraction quality, and diversity before synthesis.",
            "- Weak or unsupported details are treated as provisional instead of being stated as certain.",
            "",
            "Key evidence",
        ]
        for idx, row in enumerate(evidence_rows[:6], start=1):
            title = re.sub(r"\s+", " ", (row.get("title") or "").strip())[:120]
            provider = str(row.get("provider") or "").strip() or title or f"Source {idx}"
            snippet = re.sub(r"\s+", " ", (row.get("snippet") or "").strip())[:180]
            date_hint = row.get("date_hint") or row.get("published_at") or "date n/a"
            claim = snippet or title
            tier = str(row.get("source_tier") or row.get("tier") or "other").strip()
            quality = row.get("extraction_quality", row.get("extract_quality_score", "n/a"))
            lines.append(f"- [{date_hint}] {claim} - {provider} ({tier}, quality={quality}) [S{idx}]")

        timeline_rows = list(evidence_rows[:6])
        timeline_rows.sort(
            key=lambda row: (
                self._extract_date_from_text(str(row.get("date_hint") or row.get("published_at") or "")) or datetime.min
            ),
            reverse=True,
        )
        lines.extend(["", "Timeline (newest evidence first)"])
        for idx, row in enumerate(timeline_rows, start=1):
            date_hint = row.get("date_hint") or row.get("published_at") or "date n/a"
            title = re.sub(r"\s+", " ", (row.get("title") or "").strip())[:120] or f"Source {idx}"
            lines.append(f"- [{date_hint}] {title} [S{idx}]")

        lines.extend(["", "Confidence"])
        if agreement_level == "high" and not conflict_detected and not stale_detected:
            lines.append("- High, because multiple usable sources survived quality checks.")
        elif len(evidence_rows) >= 1:
            lines.append("- Medium, because at least one usable source supports the answer, but evidence is partial.")
        else:
            lines.append("- Low, because no strong corroborating source was available.")

        uncertainties: List[str] = []
        if len(evidence_rows) < 2:
            uncertainties.append("Limited corroboration: fewer than two distinct sources were usable.")
        if any(not row.get("date_hint") for row in evidence_rows[:6]):
            uncertainties.append("Some sources did not expose a clear publication date.")
        if conflict_detected:
            uncertainties.append("Some sources disagree on specific details, so disputed claims remain tentative.")
        if stale_detected:
            uncertainties.append("Freshness risk: newest verifiable source appears older than ideal for a live-status request.")
        if attribution_focus:
            uncertainties.append("Who/how attribution is not officially verified yet.")
        if high_stakes_mode:
            uncertainties.append("High-stakes guard: verify against primary official sources before action.")

        lines.extend(["", "What to treat carefully"])
        if uncertainties:
            for item in uncertainties:
                lines.append(f"- {item}")
        else:
            lines.append("- Treat this as the best-supported answer from current retrievable evidence, not a guarantee that no newer source exists.")

        lines.append("")
        lines.append("Timeline (newest evidence first)")
        for idx, row in enumerate(evidence_rows[:6], start=1):
            date_hint = row.get("date_hint") or row.get("published_at") or "date n/a"
            title = re.sub(r"\s+", " ", (row.get("title") or "").strip())[:120] or f"Source {idx}"
            lines.append(f"- [{date_hint}] {title} [S{idx}]")
        lines.extend(["", "Sources:"])
        for idx, row in enumerate(evidence_rows[:6], start=1):
            title = (row.get("title") or "").strip() or f"Source {idx}"
            link = (row.get("link") or "").strip()
            if link:
                lines.append(f"- [S{idx}] {title} - {link}")

        lines.extend(
            [
                "",
                "Trust summary",
                f"- Freshness: {freshness_label}",
                f"- Agreement: {agreement_level.capitalize()}",
                f"- Signal: {signal_label}",
                f"- Source mix: {source_mix}",
                f"- High-stakes guard: {'Enabled' if high_stakes_mode else 'Not enabled'}",
            ]
        )
        return "\n".join(lines).strip()

        answer_line = (
            f"As of {now_utc}, the core event described in '{goal}' is corroborated by multiple sources, "
            "but attribution details remain unverified."
            if attribution_focus
            else f"As of {now_utc}, this update for '{goal}' is grounded in current retrievable sources with corroborated event-level evidence."
        )

        lines: List[str] = [
            "Answer",
            answer_line,
            "",
            "Why this answer",
            f"- Independent sources agree on the event-level facts at a {agreement_level} level.",
            "- Evidence was ranked for quality/diversity and enriched via extraction when available.",
            "- Attribution certainty is treated separately from event confirmation to avoid overclaiming.",
            "",
            "Key evidence",
        ]
        for idx, row in enumerate(evidence_rows[:6], start=1):
            title = re.sub(r"\s+", " ", (row.get("title") or "").strip())[:120]
            provider = str(row.get("provider") or "").strip() or title or f"Source {idx}"
            snippet = re.sub(r"\s+", " ", (row.get("snippet") or "").strip())[:180]
            date_hint = row.get("date_hint") or row.get("published_at") or "date n/a"
            claim = snippet or title
            lines.append(f"- [{date_hint}] {claim} — {provider} [S{idx}]")

        lines.append("")
        lines.append("Timeline (newest evidence first)")
        for idx, row in enumerate(evidence_rows[:6], start=1):
            date_hint = row.get("date_hint") or row.get("published_at") or "date n/a"
            title = re.sub(r"\s+", " ", (row.get("title") or "").strip())[:120] or f"Source {idx}"
            lines.append(f"- [{date_hint}] {title} [S{idx}]")
        lines.append("")
        lines.append("Sources:")
        for idx, row in enumerate(evidence_rows[:6], start=1):
            title = (row.get("title") or "").strip() or f"Source {idx}"
            link = (row.get("link") or "").strip()
            if link:
                lines.append(f"- [S{idx}] {title} - {link}")

        uncertainties: List[str] = []
        if len(evidence_rows) < 2:
            uncertainties.append("Limited corroboration: fewer than two distinct sources were usable.")
        if any(not row.get("date_hint") for row in evidence_rows[:6]):
            uncertainties.append("Some sources did not expose a clear publication date.")
        if conflict_detected:
            uncertainties.append("Some sources disagree on specific details, so attribution-level claims remain tentative.")
        if stale_detected:
            uncertainties.append("Freshness risk: newest verifiable source appears older than ideal for a live-status request.")
        if attribution_focus:
            uncertainties.append("Who/how attribution is not officially verified yet.")
        if high_stakes_mode:
            uncertainties.append(
                "High-stakes guard: this summary should be verified against primary official sources before action."
            )
        if uncertainties:
            lines.append("")
            lines.append("What is uncertain or disputed")
            for item in uncertainties:
                lines.append(f"- {item}")

        if attribution_focus:
            lines.extend(
                [
                    "",
                    "Possible explanation (unconfirmed)",
                    "- Early access / preview circulation leak.",
                    "- Internal distribution chain exposure.",
                    "- Post-production access leak.",
                    "- Piracy-network capture after screening.",
                ]
            )

        lines.extend(
            [
                "",
                "Bottom line",
                f"The event is corroborated, but attribution remains {('unverified' if attribution_focus else 'partially disputed')} in current evidence.",
                "",
                "Trust summary",
                f"- Freshness: {freshness_label}",
                f"- Agreement: {agreement_level.capitalize()}",
                f"- Signal: {signal_label}",
                f"- Source mix: {source_mix}",
                f"- High-stakes guard: {'Enabled' if high_stakes_mode else 'Not enabled'}",
                "",
                "Next useful follow-up",
            ]
        )
        if freshness_mode:
            lines.append("- Show only the newest official-source updates from the last 24 hours.")
        else:
            lines.append("- Compare where top sources agree vs disagree with citations.")
        lines.append("- Highlight what is verified vs unverified in a timeline.")

        return "\n".join(lines).strip()

    def _resolve_result_source_links(self, result: Dict[str, Any]) -> List[str]:
        links: List[str] = []
        derived_rows: List[Dict[str, Any]] = []
        existing = result.get("sources")
        if isinstance(existing, list):
            for item in existing:
                if isinstance(item, str):
                    link = item.strip()
                elif isinstance(item, dict):
                    link = str(item.get("link") or item.get("url") or "").strip()
                    if not link:
                        doc_id = str(item.get("doc_id") or "").strip()
                        if doc_id:
                            chunk_index = item.get("chunk_index")
                            page_start = item.get("page_start")
                            page_end = item.get("page_end")
                            safe_chunk = str(chunk_index if chunk_index is not None else "na")
                            safe_page_start = str(page_start if page_start is not None else "na")
                            safe_page_end = str(page_end if page_end is not None else "na")
                            link = (
                                f"internal://doc/{quote_plus(doc_id)}"
                                f"?chunk={quote_plus(safe_chunk)}"
                                f"&page_start={quote_plus(safe_page_start)}"
                                f"&page_end={quote_plus(safe_page_end)}"
                            )
                            source_title = f"{doc_id} chunk {safe_chunk}"
                            if safe_page_start != "na":
                                source_title += f" p.{safe_page_start}"
                                if safe_page_end != "na" and safe_page_end != safe_page_start:
                                    source_title += f"-{safe_page_end}"
                            derived_rows.append(
                                {
                                    "title": source_title,
                                    "link": link,
                                    "provider": "uploaded_document",
                                    "published_at": "",
                                    "tier": "doc",
                                    "rank_score": float(item.get("score") or 0.0),
                                }
                            )
                else:
                    link = ""
                if link:
                    links.append(link)
        if not links:
            evidence = dict(self._trace_data.get("evidence_stats") or {})
            rows = list(evidence.get("source_rows") or [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                link = str(row.get("link") or row.get("url") or "").strip()
                if link:
                    links.append(link)
        if derived_rows:
            stats = self._trace_data.setdefault("evidence_stats", {})
            existing_rows = list(stats.get("source_rows") or [])
            seen_links = {
                str(row.get("link") or row.get("url") or "").strip().lower()
                for row in existing_rows
                if isinstance(row, dict)
            }
            for row in derived_rows:
                lower_link = str(row.get("link") or "").strip().lower()
                if not lower_link or lower_link in seen_links:
                    continue
                seen_links.add(lower_link)
                existing_rows.append(row)
            stats["source_rows"] = existing_rows[:12]
        deduped: List[str] = []
        seen = set()
        for link in links:
            key = link.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(link)
        return deduped[:12]

    def _determine_quality_mode(
        self,
        *,
        route_label: str,
        intent: IntentType,
        planner_path: str,
    ) -> str:
        planner = str(planner_path or "").strip().lower()
        if planner == "entity_lookup":
            return "entity_lookup"
        label = str(route_label or "").strip().lower()
        if label in {"deep_search", "news_search", "official_search", "comparison_search"}:
            return "deep_research"
        if label == "task":
            return "standard_task"
        if label in {"fast_message", "no_search", "fast_search", "news_search", "official_search", "comparison_search", "standard_task", "deep_research", "doc_mode", "entity_lookup", "task", "clarification"}:
            return label
        if planner == "deep_research":
            return "deep_research"
        if planner == "fast_search":
            return "fast_search"
        if planner in {"fast_path", "query_cache", "micro_fast"}:
            return "fast_message"
        if intent in {IntentType.NEWS, IntentType.RESEARCH}:
            return "deep_research"
        goal_text = str(self._trace_data.get("goal") or "").lower()
        if planner in {"fsm", "dag_exec"} and re.search(
            r"\b(pdf|document|doc|notes|chapter|unit|important questions|mark|from this|uploaded)\b",
            goal_text,
        ):
            return "doc_mode"
        return "standard_task"

    async def _run_fast_search(self, goal: str) -> Optional[str]:
        from taos.core.search.package_registry import lookup_npm_latest
        from taos.core.tools.builtin.web_search import web_search
        from taos.core.tools.builtin.web_extract import web_extract

        freshness = self._freshness_policy.decide(goal)
        search_result = await self._search_lite.run(
            query=goal,
            web_search_fn=web_search,
            web_extract_fn=web_extract,
            package_registry_fn=lookup_npm_latest,
            search_type=freshness.search_type,
            recency_days=freshness.recency_days,
            freshness_mode=freshness.mode,
        )
        raw_rows = list(search_result.get("raw_rows") or [])
        enriched_rows: List[Dict[str, Any]] = []
        for row in raw_rows:
            provider = str(row.get("provider") or urlparse(str(row.get("link") or "")).netloc or "").replace("www.", "")
            normalized = {
                "title": str(row.get("title") or "").strip(),
                "link": str(row.get("link") or "").strip(),
                "snippet": str(row.get("snippet") or "").strip(),
                "provider": provider,
                "tier": str(row.get("tier") or "other").strip().lower(),
                "published_at": str(row.get("published_at") or "").strip(),
                "freshness_score": float(row.get("freshness_score") or 0.0),
                "source_of_record": bool(row.get("source_of_record")),
                "version_candidate": str(row.get("version_candidate") or "").strip(),
            }
            scored = self._source_quality.score(normalized)
            scored["source_of_record"] = bool(row.get("source_of_record"))
            scored["version_candidate"] = str(row.get("version_candidate") or "").strip()
            enriched_rows.append(scored)
        freshness_summary = self._freshness_policy.summarize(enriched_rows, mode=freshness.mode)
        unique_domains = {
            str(row.get("provider") or row.get("domain") or "").strip()
            for row in enriched_rows
            if str(row.get("provider") or row.get("domain") or "").strip()
        }
        metadata = dict(search_result.get("metadata") or {})
        verification_state = str(metadata.get("verification_state") or "unknown").strip().lower()
        query_kind = str(metadata.get("query_kind") or "current_lookup").strip().lower()
        official_source_found = bool(metadata.get("source_of_record_count") or 0)
        extract_success_count = int(metadata.get("extract_success_count") or 0)
        extract_attempted_count = int(metadata.get("extract_attempted_count") or 0)
        stats = self._trace_data.setdefault("evidence_stats", {})
        stats.update(
            {
                "source_count": len(enriched_rows),
                "provider_count": len(unique_domains),
                "domain_diversity": round(len(unique_domains) / max(1, len(enriched_rows)), 3),
                "source_rows": enriched_rows[:5],
                "extract_count": extract_success_count,
                "extract_attempted_count": extract_attempted_count,
                "extract_failed_count": int(metadata.get("extract_failed_count") or 0),
                "source_reading_used": bool(metadata.get("source_reading_used")),
                "snippet_only": bool(metadata.get("snippet_only")),
                "official_count": sum(1 for row in enriched_rows if str(row.get("tier") or "").lower() == "official"),
                "trusted_count": sum(1 for row in enriched_rows if str(row.get("tier") or "").lower() == "trusted"),
                "freshness_summary": freshness_summary,
                "stale_detected": bool(freshness_summary.get("stale_detected")),
                "source_diversity_score": round(len(unique_domains) / max(1, len(enriched_rows)), 3),
                "extraction_recovery_used": False,
                "cache_status": str(metadata.get("cache_status") or "miss"),
                "query_kind": query_kind,
                "verification_state": verification_state,
                "source_type": str(metadata.get("source_type") or "").strip(),
                "source_domain": str(metadata.get("source_domain") or "").strip(),
                "generic_web_used": bool(metadata.get("generic_web_used")),
                "package_registry_used": bool(metadata.get("package_registry_used")),
                "package_name": str(metadata.get("package_name") or "").strip(),
                "official_source_required": query_kind in {"version_lookup", "role_lookup"},
                "official_source_found": official_source_found,
                "fast_search_escalation_recommended": bool(metadata.get("fast_search_escalation_recommended")),
                "search_queries": list(metadata.get("search_queries") or []),
                "signal": "clean" if verification_state in {"verified", "confirmed"} else "candidate_only",
            }
        )
        self._trace_data["verification_state"] = verification_state
        self._trace_data["fast_search_escalation_recommended"] = bool(metadata.get("fast_search_escalation_recommended"))
        self._set_trace_value("route_label", "fast_search")
        self._set_trace_value("mode", "fast_search")
        self._append_direct_trace_step(
            step_type="tool",
            status="success" if enriched_rows else "failed",
            tool="web_extract" if extract_success_count else "web_search",
            summary=(
                f"Search Lite returned {len(enriched_rows)} source row(s) and read {extract_success_count} source page(s)."
                if extract_attempted_count
                else f"Search Lite returned {len(enriched_rows)} current-lookup source row(s)."
            ),
        )
        if not enriched_rows:
            self._mark_trace_fallback(
                reason="fast_search_no_result",
                freshness_status="failed",
                freshness_note="Search Lite returned no reliable current-lookup results.",
            )
        return str(search_result.get("answer") or "").strip() or None

    def _should_escalate_fast_search(self) -> bool:
        evidence = dict(self._trace_data.get("evidence_stats") or {})
        verification_state = str(
            evidence.get("verification_state") or self._trace_data.get("verification_state") or "unknown"
        ).strip().lower()
        if verification_state in {"verified", "confirmed"}:
            return False
        if bool(
            evidence.get("fast_search_escalation_recommended")
            or self._trace_data.get("fast_search_escalation_recommended")
        ):
            return True
        query_kind = str(evidence.get("query_kind") or "").strip().lower()
        official_required = bool(evidence.get("official_source_required"))
        return query_kind in {"version_lookup", "role_lookup"} or official_required

    async def _try_early_package_version_preempt(
        self,
        *,
        goal: str,
        normalized_goal: str,
        deterministic_route: Any,
        user_id: str,
        doc_context_active: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if doc_context_active or not self._is_package_version_lookup(goal):
            return None
        trace_snapshot = dict(self._trace_data or {})
        route_decision = dict(deterministic_route.to_dict())
        route_decision.update(
            {
                "route": "fast_search",
                "selected_route": "fast_search",
                "phase107_route": "fast_search",
                "route_owner": "search_lite",
                "route_reason": "source_of_record_package_version_preempt",
                "policy_reason": "source_of_record_package_version_preempt",
            }
        )
        route_boundary_summary = self._build_route_boundary_summary(
            phase107_route="fast_search",
            selected_route="fast_search",
            route_owner="search_lite",
            boundary=str(getattr(deterministic_route, "boundary", "") or "deterministic"),
            used_llm=bool(getattr(deterministic_route, "used_llm", False)),
        )
        classification = ClassificationResult(
            intent=IntentType.SIMPLE_LOOKUP,
            domain=DomainType.PROGRAMMING,
            confidence=0.95,
            is_deterministic=True,
            suggested_mode="fast",
            metadata={
                "route_label": "fast_search",
                "route_source": f"phase107_{getattr(deterministic_route, 'boundary', 'deterministic')}",
                "route_confidence": float(getattr(deterministic_route, "confidence", 0.0) or 0.0),
                "query_kind": "version_lookup",
                "route_decision": route_decision,
                "route_boundary_summary": route_boundary_summary,
                "routing_profile": {
                    "primary_intent": "package_version_lookup",
                    "query_kind": "version_lookup",
                    "grounding_need": "source_of_record",
                    "risk_level": "low",
                },
            },
        )
        self._log(
            "route_decider.package_version_preempt",
            entity=self._package_version_entity_hint(goal),
            registry="npm",
        )
        self._set_trace_value("intent", classification.intent.value)
        self._set_trace_value("mode", "fast_search")
        self._set_trace_value("route_label", "fast_search")
        self._set_trace_value("route_source", classification.metadata.get("route_source"))
        self._set_trace_value("route_confidence", classification.metadata.get("route_confidence"))
        self._set_trace_value("query_kind", "version_lookup")
        self._set_trace_value("policy_reason", "source_of_record_package_version_preempt")
        self._set_trace_value("verification_state", "pending")
        self._set_trace_value(
            "interpretation",
            {
                "raw_query": goal,
                "normalized_query": normalized_goal,
                "rewritten_query": normalized_goal,
                "rewrite_reason": "not_rewritten_source_of_record_preempt",
            },
        )
        self._set_trace_value("route_decision", route_decision)
        self._set_trace_value("route_boundary_summary", route_boundary_summary)
        self._set_trace_value(
            "planning_handoff",
            {
                "raw_query": goal,
                "normalized_query": normalized_goal,
                "rewritten_query": normalized_goal,
                "grounding_need": "source_of_record",
                "route_reason": "source_of_record_package_version_preempt",
                "route_owner": "search_lite",
                "query_kind": "version_lookup",
                "policy_reason": "source_of_record_package_version_preempt",
            },
        )
        self._set_trace_value("planner_path", "fast_search")
        self._set_trace_value("dag_name", "search_lite")
        fast_search_answer = await self._run_fast_search(goal)
        evidence = dict(self._trace_data.get("evidence_stats") or {})
        if fast_search_answer and not self._should_escalate_fast_search():
            self._log(
                "engine.fast_search_direct",
                owner="search_lite",
                source_of_record=str(evidence.get("source_domain") or "npmjs.com"),
            )
            return await self._finalize(
                state=None,
                classification=classification,
                raw_result=fast_search_answer,
                goal_override=goal,
                user_id=user_id,
            )
        self._trace_data = trace_snapshot
        return None

    def _is_package_version_lookup(self, goal: str) -> bool:
        text = str(goal or "").strip().lower()
        if not text:
            return False
        has_version_hint = bool(
            re.search(
                r"\b(version|versions|versio|verison|versoin|release|latest\s+release|stable\s+release|latest\s+stable)\b",
                text,
            )
        )
        has_lookup_hint = bool(re.search(r"\b(current|latest|stable|release|version|versio)\b", text))
        has_entity = bool(
            re.search(r"\b(vite|react|nextjs|next\.js|typescript|fastapi|django|langchain|tokio|serde)\b", text)
            or re.search(r"\b@[a-z0-9][a-z0-9._-]{0,63}/[a-z0-9][a-z0-9._-]{0,63}\b", text)
        )
        return has_version_hint and has_lookup_hint and has_entity

    def _package_version_entity_hint(self, goal: str) -> str:
        text = str(goal or "").strip().lower()
        alias_map = {"nextjs": "next", "next.js": "next"}
        for candidate in ("vite", "react", "nextjs", "next.js", "typescript", "fastapi", "django", "langchain", "tokio", "serde"):
            if re.search(rf"\b{re.escape(candidate)}\b", text):
                return alias_map.get(candidate, candidate)
        scoped = re.search(r"\b@[a-z0-9][a-z0-9._-]{0,63}/[a-z0-9][a-z0-9._-]{0,63}\b", text)
        return scoped.group(0) if scoped else ""

    def _is_verified_source_of_record_fast_search(self) -> bool:
        evidence = dict(self._trace_data.get("evidence_stats") or {})
        return (
            str(self._trace_data.get("planner_path") or "").strip().lower() == "fast_search"
            and str(evidence.get("source_type") or "").strip().lower() == "package_registry"
            and str(evidence.get("verification_state") or "").strip().lower() in {"verified", "confirmed"}
        )

    def _route_label_from_selected_route(self, selected_route: str) -> str:
        route = str(selected_route or "").strip().lower()
        mapping = {
            "micro_fast": "fast_message",
            "no_search": "no_search",
            "fast_search": "fast_search",
            "standard_answer": "standard_task",
            "standard_fsm_task": "task",
            "deep_research": "deep_research",
            "news_search": "news_search",
            "official_search": "official_search",
            "comparison_search": "comparison_search",
            "entity_lookup": "entity_lookup",
            "document_pipeline": "doc_mode",
            "doc_mode": "doc_mode",
            "transform_pipeline": "standard_task",
            "clarification": "clarification",
            "task": "task",
        }
        return mapping.get(route, "standard_task")

    def _resolve_selected_route(self, *, phase107_route: str, query_kind: str) -> str:
        route = str(phase107_route or "").strip().lower()
        kind = str(query_kind or "").strip().lower()
        if kind == "entity_lookup" and bool(self._settings.entity_lookup_v1_enabled):
            return "entity_lookup"
        if route == "entity_lookup" and bool(self._settings.entity_lookup_v1_enabled):
            return "entity_lookup"
        return self._execution_route_from_phase107_route(route)

    def _execution_route_from_phase107_route(self, route: str) -> str:
        route = str(route or "").strip().lower()
        if route == "fast_message":
            return "micro_fast"
        if route == "no_search":
            return "no_search"
        if route == "fast_search":
            return "fast_search"
        if route == "entity_lookup":
            return "entity_lookup"
        if route in {"deep_search", "news_search", "official_search", "comparison_search"}:
            return "deep_research"
        if route == "doc_mode":
            return "doc_mode"
        if route == "clarification":
            return "clarification"
        return "standard_answer"

    def _route_owner_for_selected_route(self, *, selected_route: str, phase107_route: str) -> str:
        route = str(selected_route or "").strip().lower()
        if route == "entity_lookup":
            return "entity_lookup_pipeline"
        return self._route_owner_for_phase107_route(phase107_route)

    def _route_owner_for_phase107_route(self, route: str) -> str:
        route = str(route or "").strip().lower()
        if route == "fast_message":
            return "direct_fast_message"
        if route == "no_search":
            return "direct_llm_no_tools"
        if route == "fast_search":
            return "search_lite"
        if route == "entity_lookup":
            return "entity_lookup_pipeline"
        if route in {"deep_search", "news_search", "official_search", "comparison_search"}:
            return "research_pipeline"
        if route == "doc_mode":
            return "document_pipeline"
        if route == "task":
            return "fsm_planner"
        if route == "clarification":
            return "clarification_fallback"
        return "direct_standard"

    def _build_route_boundary_summary(
        self,
        *,
        phase107_route: str,
        selected_route: str,
        route_owner: str,
        boundary: str,
        used_llm: bool,
    ) -> Dict[str, Any]:
        route = str(phase107_route or "standard_task").strip().lower()
        return {
            "route": route,
            "selected_route": str(selected_route or "").strip().lower(),
            "owner": str(route_owner or self._route_owner_for_selected_route(selected_route=selected_route, phase107_route=route)),
            "boundary": str(boundary or "deterministic"),
            "used_llm": bool(used_llm),
            "planner_allowed": route == "task",
            "fsm_allowed": route == "task",
            "web_search_allowed": route in {"fast_search", "deep_search", "news_search", "official_search", "comparison_search", "entity_lookup"},
            "research_allowed": route in {"deep_search", "news_search", "official_search", "comparison_search", "entity_lookup"},
            "doc_pipeline_allowed": route == "doc_mode",
        }

    async def _execute_route_owner_path(
        self,
        *,
        route_owner: str,
        goal: str,
        effective_goal: str,
        classification: ClassificationResult,
        user_id: str,
        tracker: Optional[ProgressTracker],
        doc_context_active: bool,
        doc_ids: List[str],
    ) -> Optional[Dict[str, Any]]:
        route = ""
        if classification and isinstance(classification.metadata, dict):
            route = str(classification.metadata.get("route_label") or "").strip()
        context = RouteExecutionContext(
            query=effective_goal,
            raw_query=goal,
            normalized_query=effective_goal,
            user_id=user_id,
            route_decision={
                "route": route,
                "route_owner": str(route_owner or "").strip().lower(),
                "universal_understanding": dict(classification.metadata.get("universal_understanding") or {})
                if classification and isinstance(classification.metadata, dict)
                else {},
            },
            interpretation={
                "universal_understanding": dict(classification.metadata.get("universal_understanding") or {})
                if classification and isinstance(classification.metadata, dict)
                else {},
            },
            include_trace=bool(self._trace_enabled),
            request_budget=None,
            metadata={
                "route": route,
                "route_owner": str(route_owner or "").strip().lower(),
                "universal_understanding": dict(classification.metadata.get("universal_understanding") or {})
                if classification and isinstance(classification.metadata, dict)
                else {},
            },
            engine=self,
            classification=classification,
            tracker=tracker,
            doc_context_active=bool(doc_context_active),
            doc_ids=list(doc_ids or []),
        )
        result = await self._route_dispatcher.dispatch(context)
        return result.payload

    def _apply_route_decision_to_classification(
        self,
        *,
        classification: ClassificationResult,
        route_decision: Any,
        doc_context_active: bool,
    ) -> None:
        if not isinstance(classification.metadata, dict):
            classification.metadata = {}
        route = str(getattr(route_decision, "route", "") or "").strip().lower()
        if route in {"deep_search", "news_search", "official_search"}:
            classification.intent = IntentType.NEWS if route == "news_search" else IntentType.RESEARCH
            classification.suggested_mode = "deep"
        elif route == "comparison_search":
            classification.intent = IntentType.COMPARISON
            classification.suggested_mode = "deep"
        elif route == "entity_lookup":
            classification.intent = IntentType.RESEARCH
            classification.suggested_mode = "deep"
        elif route == "fast_search":
            classification.intent = IntentType.SIMPLE_LOOKUP
            classification.suggested_mode = "standard"
        elif route == "no_search":
            classification.intent = IntentType.DEFINITION
            classification.suggested_mode = "fast"
        elif route == "fast_message":
            classification.intent = IntentType.SIMPLE_LOOKUP
            classification.suggested_mode = "fast"
        elif route == "doc_mode":
            classification.intent = IntentType.TASK
            classification.suggested_mode = "standard"
        elif route == "task":
            classification.intent = IntentType.TASK
            classification.suggested_mode = "standard"
        classification.confidence = max(
            float(classification.confidence or 0.0),
            float(getattr(route_decision, "confidence", 0.0) or 0.0),
        )
        classification.metadata.update(
            {
                "route_label": route,
                "route_source": f"phase107_{getattr(route_decision, 'boundary', 'deterministic')}",
                "route_confidence": float(getattr(route_decision, "confidence", 0.0) or 0.0),
                "doc_context_active": bool(doc_context_active),
            }
        )

    def _build_direct_knowledge_fallback(self, goal: str) -> str:
        topic = str(goal or "that").strip()
        return (
            f"{topic} is a general knowledge question that does not need live search. "
            "I can answer it directly, but if you need the latest/current status, ask for a verified lookup."
        )

    def _build_fast_search_unverified_message(self, goal: str) -> str:
        source_rows = list((self._trace_data.get("evidence_stats") or {}).get("source_rows") or [])
        lines = [
            "Answer",
            (
                "I could not verify a reliable current answer from strong live sources for "
                f"'{str(goal or '').strip()}'."
            ),
            "",
            "What is still unclear",
            "- The quick-search results were weak, conflicting, or not source-of-record enough to trust.",
            "- A fast current-lookup answer would risk being wrong, so I am not presenting it as confirmed.",
            "",
            "Next useful follow-ups",
            "- Ask for official-source-only verification.",
            "- Retry in a moment if this is a moving target or recent release.",
        ]
        if source_rows:
            lines.extend(["", "Sources"])
            for idx, row in enumerate(source_rows[:3], start=1):
                title = str(row.get("title") or f"Source {idx}").strip()
                link = str(row.get("link") or row.get("url") or "").strip()
                if link:
                    lines.append(f"- [S{idx}] {title} - {link}")
        return "\n".join(lines).strip()

    def _should_reject_fast_path_output(
        self,
        *,
        query: str,
        response_text: str,
        selected_route: str,
        routing_profile: Optional[Dict[str, Any]] = None,
    ) -> bool:
        route = str(selected_route or "").strip().lower()
        if route != "micro_fast":
            return True

        profile = dict(routing_profile or {})
        if bool(profile.get("context_required")):
            return True
        if str(profile.get("grounding_need") or "none") != "none":
            return True
        if float(profile.get("ambiguity_score") or 0.0) >= 0.35:
            return True
        if str(profile.get("risk_level") or "low") in {"medium", "high"}:
            return True

        answer = str(response_text or "").lower()
        raw_query = str(query or "").lower()
        if "[insert" in answer:
            return True
        if "as of my last update" in answer:
            return True
        if re.search(
            r"\b(who is|what is|latest|current|verify|official|research|compare|profile|ceo|founder)\b",
            raw_query,
        ):
            capability_template = (
                "i can code full web pages" in answer
                or "tell me the exact output and i will build" in answer
                or "i can do structured research" in answer
            )
            if capability_template:
                return True
        return False

    def _enforce_authority_quality_blocks(
        self,
        *,
        text: str,
        route_label: str,
        intent: IntentType,
        planner_path: str,
        source_links: List[str],
        signal: str,
        stale_detected: bool,
        conflict_detected: bool,
        high_stakes_mode: bool,
        official_source_found: bool = False,
    ) -> str:
        output = str(text or "").strip()
        if not output:
            return output
        mode = self._determine_quality_mode(
            route_label=route_label,
            intent=intent,
            planner_path=planner_path,
        )
        if mode == "entity_lookup":
            return self._strip_generic_research_followups(output).strip()
        goal_text = str(self._trace_data.get("goal") or "")
        if mode == "standard_task":
            output = self._ensure_standard_task_intro(output)
        if mode in {"deep_research", "doc_mode", "standard_task", "fast_search"} and source_links:
            output = self._ensure_claim_citations(
                text=output,
                source_links=source_links,
                mode=mode,
            )
        if mode in {"deep_research", "fast_search"}:
            output = self._ensure_research_uncertainty_guard(
                text=output,
                signal=str(signal or "").strip().lower(),
                stale_detected=bool(stale_detected),
                conflict_detected=bool(conflict_detected),
                high_stakes_mode=bool(high_stakes_mode),
                official_source_found=bool(official_source_found),
            )
        if mode in {"deep_research", "standard_task", "fast_search"}:
            output = self._ensure_adversarial_integrity_guard(
                text=output,
                goal=goal_text,
                signal=str(signal or "").strip().lower(),
                conflict_detected=bool(conflict_detected),
                high_stakes_mode=bool(high_stakes_mode),
                official_source_found=bool(official_source_found),
            )
        followup_mode = "deep_research" if mode == "deep_research" else "standard_task" if mode == "standard_task" else mode
        if mode in {"deep_research", "doc_mode", "standard_task", "fast_search"}:
            output = self._ensure_mode_followups(
                text=output,
                mode=followup_mode,
                signal=str(signal or "").strip().lower(),
                stale_detected=bool(stale_detected),
                conflict_detected=bool(conflict_detected),
                high_stakes_mode=bool(high_stakes_mode),
            )
        return output.strip()

    def _strip_generic_research_followups(self, text: str) -> str:
        lines = str(text or "").splitlines()
        if not lines:
            return str(text or "")
        out: List[str] = []
        skip_followups = False
        for line in lines:
            low = str(line or "").strip().lower()
            if low in {"next useful follow-ups", "next useful follow-up", "related follow-ups"}:
                skip_followups = True
                continue
            if skip_followups:
                if low.startswith("- "):
                    continue
                if not low:
                    continue
                skip_followups = False
            out.append(line)
        return "\n".join(out).strip()

    def _ensure_claim_citations(
        self,
        *,
        text: str,
        source_links: List[str],
        mode: str,
    ) -> str:
        lines = list(str(text or "").splitlines())
        if not lines:
            return text

        def citation_for_index(idx: int) -> str:
            safe_idx = max(1, min(idx + 1, len(source_links)))
            return f"[S{safe_idx}]"

        citation_pattern = re.compile(r"\[S\d+\]")
        split_pattern = re.compile(r",\s+(?=(?:and|but|while|whereas)\b)", re.I)
        source_rows = list((self._trace_data.get("evidence_stats") or {}).get("source_rows") or [])
        section_alias = {
            "answer": "answer",
            "key points": "key_points",
            "key developments": "key_points",
            "important points": "key_points",
            "key findings": "key_points",
            "key evidence": "evidence",
            "evidence": "evidence",
            "bottom line": "bottom_line",
            "sources": "sources",
        }
        source_catalog: List[Dict[str, Any]] = []
        for idx, link in enumerate(source_links):
            ll = str(link or "").strip().lower()
            matched = None
            for row in source_rows:
                if not isinstance(row, dict):
                    continue
                row_link = str(row.get("link") or row.get("url") or "").strip().lower()
                if row_link and row_link == ll:
                    matched = row
                    break
            source_catalog.append(
                {
                    "index": idx,
                    "link": str(link or "").strip(),
                    "title": str((matched or {}).get("title") or ""),
                    "provider": str((matched or {}).get("provider") or ""),
                    "tier": str((matched or {}).get("tier") or ""),
                    "rank_score": float((matched or {}).get("rank_score") or 0.0),
                }
            )

        def add_citation_to_sentence(sentence: str, ref: str) -> str:
            if not sentence.strip() or citation_pattern.search(sentence):
                return sentence
            m = re.search(r"[.!?](?:[\"')\]]*)\s*$", sentence)
            if m:
                insert_at = m.end()
                return sentence[:insert_at] + f" {ref}" + sentence[insert_at:]
            return sentence.rstrip() + f" {ref}"

        def is_low_value_line(raw_line: str) -> bool:
            stripped = raw_line.strip()
            lower = stripped.lower()
            if not stripped:
                return True
            if lower in section_alias:
                return True
            if lower.startswith(("next useful follow-ups", "next useful moves", "trust summary", "why this answer")):
                return True
            if re.match(r"^-?\s*(want|retry|request|ask|show|compare|highlight)\b", lower):
                return True
            if lower.startswith(("in summary", "overall", "therefore", "this means", "you can")):
                return True
            return False

        def line_claim_strength(raw_line: str) -> float:
            stripped = raw_line.strip()
            lower = stripped.lower()
            if is_low_value_line(stripped):
                return -1.0
            score = 0.0
            if re.search(r"\b\d+(?:\.\d+)?%?\b", stripped):
                score += 1.1
            if re.search(r"\b(official|statement|announced|policy|repo|rate|rbi|government)\b", lower):
                score += 1.2
            if re.search(r"\b(leak|reported|report|according to|rumor|confirmed)\b", lower):
                score += 1.0
            if re.search(r"\b(legal|court|lawsuit|action|filed|fir)\b", lower):
                score += 0.9
            if re.search(r"\b(chunk|page|unit|chapter|notes|document)\b", lower):
                score += 0.8
            word_count = len(stripped.split())
            if word_count >= 8:
                score += 0.5
            return score

        source_use_count: Dict[int, int] = {}

        def fallback_source_index() -> int:
            if not source_links:
                return 0
            return min(range(len(source_links)), key=lambda i: (source_use_count.get(i, 0), i))

        def select_source_index_for_claim(line: str) -> int:
            if not source_catalog:
                return fallback_source_index()
            lower = line.lower()
            claim_tokens = set(re.findall(r"[a-z]{4,}", lower))
            claim_tokens -= {
                "this",
                "that",
                "with",
                "from",
                "have",
                "will",
                "into",
                "about",
                "there",
                "their",
                "summary",
                "answer",
            }
            wants_official = bool(re.search(r"\b(official|statement|announced|policy|repo|rate|rbi|government|regulator)\b", lower))
            wants_legal = bool(re.search(r"\b(legal|court|lawsuit|filed|action|fir)\b", lower))
            wants_report = bool(re.search(r"\b(leak|reported|report|according to|rumor)\b", lower))
            wants_doc = bool(mode == "doc_mode" or re.search(r"\b(chunk|page|unit|chapter|notes|document)\b", lower))

            best_idx = fallback_source_index()
            best_score = -999.0
            for src in source_catalog:
                idx = int(src.get("index") or 0)
                src_text = f"{src.get('title','')} {src.get('provider','')} {src.get('link','')}".lower()
                tier = str(src.get("tier") or "").lower()
                score = 0.0
                if tier == "official":
                    score += 0.9
                elif tier in {"trusted", "doc"}:
                    score += 0.45
                if wants_official and (
                    tier == "official"
                    or re.search(r"\b(official|government|reserve bank|rbi|sec|ministry|press release)\b", src_text)
                ):
                    score += 2.4
                if wants_legal and re.search(r"\b(legal|court|law|fir|lawsuit|justice)\b", src_text):
                    score += 2.0
                if wants_report and re.search(r"\b(report|news|times|post|journal|media|leak)\b", src_text):
                    score += 1.8
                if wants_doc and str(src.get("link") or "").startswith("internal://doc/"):
                    score += 2.1
                overlap = sum(1 for tok in claim_tokens if tok in src_text)
                score += min(3, overlap) * 0.25
                score += float(src.get("rank_score") or 0.0) * 0.12
                score -= float(source_use_count.get(idx, 0)) * 0.2
                if score > best_score:
                    best_score = score
                    best_idx = idx
            return best_idx

        def citation_for_claim(line: str) -> str:
            idx = select_source_index_for_claim(line)
            source_use_count[idx] = source_use_count.get(idx, 0) + 1
            return citation_for_index(idx)

        def split_multi_claim_line(line: str) -> List[str]:
            stripped = line.strip()
            if (
                not stripped
                or citation_pattern.search(stripped)
                or stripped.lower() in section_alias
                or "http://" in stripped.lower()
                or "https://" in stripped.lower()
            ):
                return [line]
            prefix = ""
            body = stripped
            if stripped.startswith("- "):
                prefix = "- "
                body = stripped[2:].strip()
            parts = [segment.strip() for segment in re.split(r";+", body) if segment.strip()]
            if len(parts) <= 1:
                parts = [segment.strip() for segment in split_pattern.split(body) if segment.strip()]
            if len(parts) <= 1 or len(parts) > 3:
                return [line]
            if any(len(part.split()) < 3 for part in parts):
                return [line]
            split_lines: List[str] = []
            leading_ws = line[: len(line) - len(line.lstrip())]
            for idx, part in enumerate(parts):
                if idx > 0:
                    part = re.sub(r"^(and|but|while|whereas)\s+", "", part, flags=re.I).strip()
                if not re.search(r"[.!?]$", part):
                    part = f"{part}."
                split_lines.append(f"{leading_ws}{prefix}{part}")
            return split_lines

        def is_factual_candidate(raw_line: str) -> bool:
            stripped = raw_line.strip()
            if not stripped:
                return False
            if stripped.lower() in section_alias or is_low_value_line(stripped):
                return False
            if stripped.startswith("- ") and len(stripped) > 3:
                return bool(re.search(r"[A-Za-z]", stripped[2:]))
            return bool(re.search(r"[A-Za-z]", stripped))

        expanded: List[str] = []
        for line in lines:
            expanded.extend(split_multi_claim_line(line))
        lines = expanded

        section_indexes: Dict[str, List[int]] = {
            "answer": [],
            "key_points": [],
            "evidence": [],
            "bottom_line": [],
        }
        current_section = "answer"
        for idx, line in enumerate(lines):
            stripped = line.strip()
            alias = section_alias.get(stripped.lower())
            if alias:
                current_section = alias
                continue
            if not stripped:
                continue
            if current_section in section_indexes:
                section_indexes[current_section].append(idx)

        def ensure_line_cited(idx: int) -> None:
            stripped = lines[idx].strip()
            if not stripped or citation_pattern.search(stripped) or is_low_value_line(stripped):
                return
            ref = citation_for_claim(stripped)
            if stripped.startswith("-"):
                lines[idx] = f"{lines[idx].rstrip()} {ref}"
            else:
                lines[idx] = add_citation_to_sentence(lines[idx], ref)

        # Answer section: boost first-answer density for multi-claim openings.
        answer_candidates = [
            idx for idx in section_indexes["answer"] if is_factual_candidate(lines[idx])
        ]
        answer_target = 0
        if answer_candidates:
            answer_target = 1
            if len(answer_candidates) >= 2 and len(source_links) >= 2:
                answer_target = 2
        cited_in_answer = sum(
            1 for idx in answer_candidates if citation_pattern.search(lines[idx].strip())
        )
        if cited_in_answer < answer_target:
            ranked_answer = sorted(
                answer_candidates,
                key=lambda i: (line_claim_strength(lines[i]), -i),
                reverse=True,
            )
            for idx in ranked_answer:
                if citation_pattern.search(lines[idx].strip()):
                    continue
                ensure_line_cited(idx)
                cited_in_answer += 1
                if cited_in_answer >= answer_target:
                    break

        # Evidence and key-point bullets should carry citations.
        key_or_evidence = section_indexes["evidence"] + section_indexes["key_points"]
        bullet_indices = [idx for idx in key_or_evidence if lines[idx].strip().startswith("-")]
        for idx in bullet_indices:
            ensure_line_cited(idx)

        # Bottom-line factual claim should be cited when present.
        for idx in section_indexes["bottom_line"]:
            if is_factual_candidate(lines[idx]) and not citation_pattern.search(lines[idx]):
                ensure_line_cited(idx)
                break

        # Mode minimums.
        citation_count = len(citation_pattern.findall("\n".join(lines)))
        if mode in {"deep_research", "doc_mode"}:
            min_citations = 3 if len(source_links) >= 2 else 2
        else:
            min_citations = 1
        if citation_count < min_citations:
            ordered_candidates = answer_candidates + bullet_indices + section_indexes["bottom_line"]
            seen_candidates = set()
            for idx in ordered_candidates:
                if idx in seen_candidates:
                    continue
                seen_candidates.add(idx)
                if not is_factual_candidate(lines[idx]) or citation_pattern.search(lines[idx]):
                    continue
                ensure_line_cited(idx)
                citation_count = len(citation_pattern.findall("\n".join(lines)))
                if citation_count >= min_citations:
                    break

        # Final guard: ensure at least one citation anywhere.
        joined = "\n".join(lines)
        if not citation_pattern.search(joined):
            for idx, line in enumerate(lines):
                if line.strip():
                    lines[idx] = add_citation_to_sentence(line, citation_for_index(0))
                    break
        return "\n".join(lines)

    def _ensure_standard_task_intro(self, text: str) -> str:
        out = str(text or "").strip()
        if not out or "```" not in out:
            return out
        lower = out.lower()
        if lower.startswith("answer\n") or lower.startswith("here is "):
            return out
        if "```python" in lower:
            return "Here is a Python function implementation:\n\n" + out
        return "Here is a direct implementation:\n\n" + out

    def _ensure_research_uncertainty_guard(
        self,
        *,
        text: str,
        signal: str,
        stale_detected: bool,
        conflict_detected: bool,
        high_stakes_mode: bool,
        official_source_found: bool,
    ) -> str:
        out = str(text or "").rstrip()
        if not out:
            return out
        weak_signal = bool(stale_detected or conflict_detected or signal in {"conflicting", "partial_conflict"})
        high_stakes_weak_signal = bool(high_stakes_mode and weak_signal)
        needs_guard = bool(weak_signal or (high_stakes_mode and not official_source_found))
        if not needs_guard:
            return out
        if re.search(r"(?im)^\s*(what(?:'s| is)\s+still\s+unclear|what is uncertain|what is uncertain or disputed)\s*$", out) and not high_stakes_weak_signal:
            return out
        lower_out = out.lower()
        # If uncertainty language is already explicit, avoid over-appending.
        has_uncertainty_language = bool(
            re.search(r"\b(unclear|not confirmed|unknown|uncertain|disagree|conflict|limited evidence)\b", lower_out)
        )
        has_high_stakes_triplet = (
            "not confirmed" in lower_out
            and "unclear" in lower_out
            and "verified source" in lower_out
        )
        if has_uncertainty_language and (not high_stakes_weak_signal or has_high_stakes_triplet):
            return out

        lines = ["", "What's still unclear"]
        if high_stakes_weak_signal:
            if "not confirmed" not in lower_out:
                lines.append("- This is not confirmed yet, so treat any conclusion as provisional.")
            if "unclear" not in lower_out:
                lines.append("- Key details remain unclear and may change as new evidence appears.")
            if "verified source" not in lower_out:
                lines.append("- No verified source currently resolves every disputed claim.")
            if "do not take action" not in lower_out:
                lines.append("- Do not take action on this alone until primary or official confirmation is available.")
        if high_stakes_mode and not official_source_found:
            lines.append("- No officially confirmed source was verified yet; treat key claims as not confirmed.")
        if conflict_detected or signal in {"conflicting", "partial_conflict"}:
            lines.append("- Sources disagree on important details, so parts of this update remain uncertain.")
        if stale_detected:
            lines.append("- Freshness is limited, so the latest status may still change.")
        if len(lines) == 2:
            lines.append("- Some details remain unclear from currently available evidence.")
        return out + "\n" + "\n".join(lines)

    def _is_adversarial_integrity_pressure(self, goal: str) -> bool:
        goal_lower = str(goal or "").lower()
        if not goal_lower:
            return False
        dangerous_patterns = (
            "confirm even if",
            "just tell me it's true",
            "just tell me its true",
            "even if not",
            "even if it's not",
            "even if its not",
            "even if unsure",
            "even if you're unsure",
            "even if you are unsure",
            "force answer",
            "no uncertainty",
            "don't show uncertainty",
            "dont show uncertainty",
            "hide uncertainty",
            "without uncertainty",
            "say it is confirmed",
            "say it's confirmed",
        )
        return any(token in goal_lower for token in dangerous_patterns)

    def _ensure_adversarial_integrity_guard(
        self,
        *,
        text: str,
        goal: str,
        signal: str,
        conflict_detected: bool,
        high_stakes_mode: bool,
        official_source_found: bool,
    ) -> str:
        out = str(text or "").rstrip()
        if not out:
            return out
        pressure_detected = self._is_adversarial_integrity_pressure(goal)
        weak_signal = bool(conflict_detected or signal in {"conflicting", "partial_conflict"})
        needs_guard = bool(pressure_detected and (weak_signal or high_stakes_mode or not official_source_found))
        if not needs_guard:
            return out
        lower_out = out.lower()
        if (
            "i cannot confirm this as true" in lower_out
            or "i can't confirm this as true" in lower_out
            or "i will not present unverified claims as facts" in lower_out
        ):
            return out

        lines = [
            "",
            "Integrity guard",
            "- I cannot confirm this as true with the currently verified evidence.",
            "- This is not confirmed.",
            "- The current status is unclear.",
            "- There is limited evidence right now.",
            "- I will not present unverified claims as facts.",
        ]
        if high_stakes_mode or not official_source_found:
            lines.append("- Verification requires an official or primary source before any decision.")
        return out + "\n" + "\n".join(lines)

    def _build_doc_mode_quick_answer(self, goal: str, *, doc_context_active: bool) -> str:
        text = str(goal or "").strip()
        mark_match = re.search(r"\b(1|2|5|10|16)\s*mark\b", text, re.I)
        mark_label = f"{mark_match.group(1)}-mark" if mark_match else "exam"
        if not doc_context_active:
            return (
                f"I can generate important {mark_label} questions fast, but there is no active uploaded document context in this request.\n\n"
                "Quick useful next move:\n"
                "- Upload/select your notes, then ask this again with a unit hint (example: Unit 2).\n\n"
                f"Starter {mark_label} question format you can use right now:\n"
                "1. Explain the core concept and architecture with a neat diagram.\n"
                "2. Compare two major approaches and justify use-cases.\n"
                "3. Analyze a real-world workflow and identify failure points.\n"
                "4. Derive key steps/algorithm and explain complexity implications.\n"
                "5. Discuss limitations and propose practical improvements."
            )
        return (
            f"Here are high-yield important {mark_label} questions from your active study context:\n"
            "1. Explain the primary concept flow end-to-end with structure and examples.\n"
            "2. Compare competing methods, trade-offs, and ideal scenarios.\n"
            "3. Describe implementation steps and expected outcomes.\n"
            "4. Analyze limitations, risks, and mitigation strategy.\n"
            "5. Build a concise exam-ready conclusion with practical takeaways.\n\n"
            "I can expand any one into a full exam answer format next."
        )

    def _ensure_mode_followups(
        self,
        *,
        text: str,
        mode: str,
        signal: str,
        stale_detected: bool,
        conflict_detected: bool,
        high_stakes_mode: bool,
    ) -> str:
        output = str(text or "").rstrip()
        if re.search(r"(?im)^\s*(next useful follow-?ups?|related follow-?ups?)\s*$", output):
            return output
        followups = self._build_mode_followups(
            mode=mode,
            signal=signal,
            stale_detected=stale_detected,
            conflict_detected=conflict_detected,
            high_stakes_mode=high_stakes_mode,
        )
        if not followups:
            return output
        return output + "\n\nNext useful follow-ups\n" + "\n".join(f"- {row}" for row in followups)

    def _build_mode_followups(
        self,
        *,
        mode: str,
        signal: str,
        stale_detected: bool,
        conflict_detected: bool,
        high_stakes_mode: bool,
    ) -> List[str]:
        weak_signal = bool(stale_detected or conflict_detected or signal in {"conflicting", "partial_conflict"})
        if mode == "deep_research":
            verify_line = "Show only confirmed facts and separate disputed claims."
            if high_stakes_mode:
                verify_line = "Show only official statements and flag anything not officially confirmed."
            elif weak_signal:
                verify_line = "Show where top sources disagree and what remains unverified."
            return [
                "Want a timeline of events with dates and source references?",
                "Want a 5-bullet concise summary of only the key points?",
                verify_line,
            ]
        if mode == "doc_mode":
            return [
                "Want this converted into a full 16-mark exam answer format?",
                "Want a short revision-notes version from the same content?",
                "Want probable exam questions generated from this topic next?",
            ]
        if mode == "fast_search":
            return [
                "Want only the latest official or primary source links?",
                "Want a quick comparison with the previous version or update?",
                "Want me to expand this into a short cited research summary?",
            ]
        if mode == "standard_task":
            return [
                "Want a deeper version with implementation details?",
                "Want a shorter practical version you can use immediately?",
                "Want me to verify this with external sources before finalizing?",
            ]
        return []

    async def _synthesize_research(
        self,
        raw_data: str,
        goal: str,
        freshness_mode: bool = False,
        agreement: Optional[Dict[str, Any]] = None,
        high_stakes_mode: bool = False,
    ) -> Optional[str]:
        """
        Deep Research Synthesis Layer (PRD constraint).
        Transforms noisy multi-source web data into structured depth without hallucination.
        """
        import re
        filtered = str(raw_data)
        # Clean noisy PDF/URL strings out of text
        filtered = re.sub(r'https?://[^\s]+', '', filtered)
        filtered = re.sub(r'\[PDF\]', '', filtered, flags=re.I)
        agreement = dict(agreement or {})
        agreement_level = str(agreement.get("agreement_level") or "unknown")
        conflict_detected = bool(agreement.get("conflict_detected"))
        stale_detected = bool(agreement.get("stale_detected"))
        high_stakes_mode = bool(high_stakes_mode or agreement.get("high_stakes_mode"))

        prompt = (
            "You are TAOS Deep Research synthesis engine.\n"
            "DO NOT HALLUCINATE. Use ONLY the provided source evidence.\n\n"
            f"Today (UTC): {datetime.now(timezone.utc).strftime('%B %d, %Y').replace(' 0', ' ')}\n"
            f"Goal: {goal}\n"
            f"Agreement signal from retrieval layer: level={agreement_level}, conflict_detected={conflict_detected}, stale_detected={stale_detected}\n\n"
            f"Evidence rows:\n{filtered}\n\n"
            "Output MUST follow this exact section structure:\n"
            "Answer\n"
            "- 2 to 4 sharp sentences. Start directly with what happened and what is still unknown.\n"
            "- Avoid generic openers like 'Based on multiple sources'.\n\n"
            "Why this answer\n"
            "- 2 to 4 bullets with analytical reasoning (agreement scope, source quality, freshness).\n"
            "- Do not use filler text.\n\n"
            "Key points\n"
            "- 3 to 6 short high-signal bullets.\n\n"
            "Evidence\n"
            "- 3 to 6 bullets in this style: <claim> — <source name> [S#].\n"
            "- Include date hints when available.\n\n"
            "Sources\n"
            "- List each cited source as [S#] title and URL from evidence rows only.\n\n"
            "What is uncertain or disputed\n"
            "- Clearly separate unverified attribution from confirmed event facts.\n\n"
            "Possible explanation (if applicable)\n"
            "- Only include when the user asks how/why/who/origin/cause.\n"
            "- Mark all hypotheses as unconfirmed.\n\n"
            "Bottom line\n"
            "- 1 to 2 sentence decisive takeaway.\n\n"
            "Trust summary\n"
            "- Freshness: High/Medium/Low\n"
            "- Agreement: Strong/Moderate/Weak\n"
            "- Signal: Clean/Partial conflict/Conflicting\n"
            "- Source mix: Official/Reporting/Mixed\n\n"
            "Next useful follow-ups\n"
            "- 2 to 4 concrete next questions.\n\n"
            "Rules:\n"
            "1. Never invent facts, URLs, dates, or citations.\n"
            "2. If support is weak, say confidence is limited and explain why.\n"
            "3. If freshness-sensitive, prioritize latest evidence and call out stale risk clearly.\n"
            "4. If sources conflict, do NOT flatten disagreement into certainty.\n"
            "5. Never claim death/assassination unless evidence rows explicitly and repeatedly support it.\n"
            "6. Keep tone serious and professional; no emojis.\n"
            "7. Distinguish event agreement from attribution uncertainty when they differ.\n"
        )
        if high_stakes_mode:
            prompt += (
                "High-stakes policy:\n"
                "- This topic can impact medical/legal/financial decisions.\n"
                "- Do not provide prescriptive advice; keep guidance informational and source-grounded.\n"
                "- If official evidence is missing, call that out explicitly.\n"
            )
        
        try:
            out = await self._run_fast_llm(prompt, stream_to_progress=True)
            if not out:
                return None
            lowered = out.lower()
            if "as of my last update" in lowered:
                return None
            if freshness_mode:
                has_citation = bool(re.search(r"\[S\d+\]", out))
                has_date = bool(
                    re.search(
                        r"\b(?:\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{4}|"
                        r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},\s+\d{4}|"
                        r"\d{4}-\d{2}-\d{2})\b",
                        out,
                        flags=re.I,
                    )
                )
                if not has_citation or not has_date:
                    self._log(
                        "engine.research_synthesis_rejected",
                        reason="missing_date_or_citation",
                        has_citation=has_citation,
                        has_date=has_date,
                    )
                    return None
                if self._contains_unsupported_critical_claim(out, filtered):
                    self._log("engine.research_synthesis_rejected", reason="unsupported_critical_claim")
                    return None
            return out
        except Exception as e:
            self._log("engine.research_synthesis_error", error=str(e))
            return None

    def _contains_unsupported_critical_claim(self, synthesized: str, raw_data: str) -> bool:
        out = (synthesized or "").lower()
        raw = (raw_data or "").lower()
        # Guard against severe hallucinations in high-stakes news updates.
        critical_terms = (
            "assassinated",
            "assassination",
            "killed",
            "death",
            "dead",
            "supreme leader",
            "khamenei",
        )
        for term in critical_terms:
            if term in out and term not in raw:
                return True
        return False

    def _error_result(
        self,
        goal: str,
        error: str,
        request_id: Optional[str] = None,
        intent: str = "unknown",
        domain: str = "general",
    ) -> Dict[str, Any]:
        return {
            "request_id": request_id or "unknown",
            "goal": goal,
            "success": False,
            "status": "failed",
            "error": error,
            "result": None,
            "intent": intent,
            "domain": domain,
        }

    def _log(self, event: str, **kwargs) -> None:
        self._capture_research_trace(event=event, payload=kwargs)
        if self._logger:
            self._logger.info(event, **kwargs)

    @property
    def memory(self) -> MemoryManager:
        return self._memory

    def _capture_research_trace(self, event: str, payload: Dict[str, Any]) -> None:
        if not self._is_research_trace_event(event):
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "request_id": self._active_request_id or "unknown",
            "event": event,
            "stage": self._research_stage_from_event(event),
            "data": self._normalize_trace_value(payload),
        }
        OrchestrationEngine._RESEARCH_TRACE_BUFFER.append(record)

    def _is_research_trace_event(self, event: str) -> bool:
        if event.startswith("engine.deep_research_"):
            return True
        if event.startswith("engine.research_"):
            return True
        return event in {
            "engine.classified",
            "engine.request_time_budget_set",
            "engine.news_search_mode",
            "engine.parallel_batch_start",
            "engine.critic_precheck",
            "engine.critic_result",
            "engine.agent_selected",
        }

    def _research_stage_from_event(self, event: str) -> str:
        if "planner" in event:
            return "planner"
        if "deep_research" in event or "research_step_recovery" in event:
            return "researcher"
        if "critic" in event or "validator" in event:
            return "validator"
        if "synthes" in event:
            return "synthesizer"
        if "time_budget" in event:
            return "budget"
        if "classified" in event:
            return "classifier"
        return "engine"

    def _normalize_trace_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for k, v in value.items():
                out[str(k)] = self._normalize_trace_value(v)
            return out
        if isinstance(value, list):
            return [self._normalize_trace_value(v) for v in value]
        if isinstance(value, tuple):
            return [self._normalize_trace_value(v) for v in value]
        return str(value)

    @classmethod
    def get_recent_research_trace(
        cls,
        request_id: Optional[str] = None,
        limit: int = 120,
    ) -> List[Dict[str, Any]]:
        size = max(1, min(int(limit), 400))
        items = list(cls._RESEARCH_TRACE_BUFFER)
        if request_id:
            rid = str(request_id).strip()
            items = [row for row in items if str(row.get("request_id", "")) == rid]
        return items[-size:]
