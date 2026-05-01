from __future__ import annotations

import json

import pytest

from taos.core.performance.progress import create_tracker, remove_tracker
from taos.core.semantic.intent_classifier import ClassificationResult, DomainType, IntentType
from taos.orchestration.engine import OrchestrationEngine


class _FakeStreamingResponse:
    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def test_extract_stream_token_string_delta():
    engine = OrchestrationEngine()
    chunk = {"choices": [{"delta": {"content": "Hello"}}]}
    assert engine._extract_stream_token(chunk) == "Hello"


def test_extract_stream_token_list_delta():
    engine = OrchestrationEngine()
    chunk = {
        "choices": [
            {
                "delta": {
                    "content": [
                        {"type": "text", "text": "Hello"},
                        {"type": "text", "text": " world"},
                    ]
                }
            }
        ]
    }
    assert engine._extract_stream_token(chunk) == "Hello world"


@pytest.mark.asyncio
async def test_consume_openrouter_stream_emits_partial_updates():
    engine = OrchestrationEngine()
    tracker = create_tracker("rid_stream_test")
    try:
        lines = [
            "data: " + json.dumps({"choices": [{"delta": {"content": "Hello"}}]}),
            "data: " + json.dumps({"choices": [{"delta": {"content": " world"}}]}),
            "data: [DONE]",
        ]
        response = _FakeStreamingResponse(lines)
        merged = await engine._consume_openrouter_stream(response=response, tracker=tracker)
        assert merged == "Hello world"
        history = tracker.history
        assert any(str(item.get("partial_result") or "").endswith("world") for item in history)
    finally:
        remove_tracker("rid_stream_test")


@pytest.mark.asyncio
async def test_finalize_skips_judge_for_fast_message_route():
    engine = OrchestrationEngine()

    async def _fail_if_called(*args, **kwargs):
        raise AssertionError("judge_and_refine should be skipped for fast_message route")

    engine._judge_system.judge_and_refine = _fail_if_called  # type: ignore[method-assign]

    classification = ClassificationResult(
        intent=IntentType.SIMPLE_LOOKUP,
        domain=DomainType.GENERAL,
        confidence=1.0,
        suggested_mode="fast",
        metadata={
            "route_label": "fast_message",
            "route_source": "heuristic",
            "route_confidence": 1.0,
        },
    )

    result = await engine._finalize(
        state=None,
        classification=classification,
        raw_result="Hey! I am here.",
        goal_override="hey",
        user_id="test_user",
    )

    assert result.get("status") == "success"
    assert isinstance(result.get("formatted_response"), str)
    assert "evaluation" not in result
