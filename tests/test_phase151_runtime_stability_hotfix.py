from __future__ import annotations

from taos.apps.api.response_contract import build_timeout_payload
from taos.core.governance.quota_manager import QuotaManager as RouteQuotaManager
from taos.core.governance.usage_meter import UsageSnapshot
from taos.core.limits.quota_manager import QuotaManager as AppQuotaManager
from taos.core.streaming.events import StreamEventType, phase_to_event_type
from taos.core.performance.progress import ProgressPhase


def test_free_quota_allows_100_requests_per_minute() -> None:
    manager = AppQuotaManager()
    final = None
    for _ in range(100):
        final = manager.check_and_consume_detailed(user_id="phase151_free", tier="free")
        assert final.allowed is True

    blocked = manager.check_and_consume_detailed(user_id="phase151_free", tier="free")
    assert final is not None
    assert final.limit_per_minute == 100
    assert blocked.allowed is False
    assert blocked.code == "RATE_LIMITED"
    assert blocked.retry_after_seconds is not None


def test_deep_search_budget_still_enforced_after_higher_app_quota() -> None:
    quota = RouteQuotaManager()
    usage = UsageSnapshot(
        route="deep_search",
        llm_calls=3,
        search_calls=6,
        extract_calls=7,
        estimated_cost_usd=0.06,
    )
    decision = quota.check(usage)
    assert decision.allowed is False
    assert "llm_call_budget_exceeded" in decision.reasons
    assert "search_call_budget_exceeded" in decision.reasons
    assert "extract_call_budget_exceeded" in decision.reasons
    assert "estimated_cost_budget_exceeded" in decision.reasons


def test_timeout_payload_includes_partial_and_trace_metadata() -> None:
    payload = build_timeout_payload(
        request_id="phase151_timeout",
        elapsed_ms=4321.0,
        partial_result="Partial answer from gathered evidence.",
        route="deep_search",
        owner="research_pipeline",
        timeout_stage="executing",
        budget_stage="executing",
        partial_answer_used=True,
        first_event_latency_ms=123.0,
        streaming_started_at=1714250000.0,
    )

    assert payload["error"] == "TIME_BUDGET_EXCEEDED"
    assert payload["answer"] == "Partial answer from gathered evidence."
    assert payload["metadata"]["timeout_stage"] == "executing"
    assert payload["metadata"]["budget_stage"] == "executing"
    assert payload["metadata"]["partial_answer_used"] is True
    assert payload["trace"]["budget_exceeded"] is True
    assert payload["trace"]["timeout_stage"] == "executing"
    assert payload["trace"]["partial_answer_used"] is True
    assert payload["trace"]["first_event_latency_ms"] == 123.0
    assert payload["trace"]["planner_path"] == "research_pipeline"


def test_streaming_progress_maps_to_progress_event() -> None:
    assert phase_to_event_type(ProgressPhase.RECEIVED.value) == StreamEventType.START
    assert phase_to_event_type(ProgressPhase.EXECUTING.value) == StreamEventType.PROGRESS
    assert phase_to_event_type(ProgressPhase.COMPLETE.value) == StreamEventType.FINAL
