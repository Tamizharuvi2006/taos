from __future__ import annotations

from taos.apps.api.response_contract import (
    build_clarification_payload,
    build_timeout_payload,
    normalize_contract_payload,
)
from taos.apps.api.schemas.agent import AgentResponse
from taos.config.env_validation import validate_runtime_environment
from taos.config.settings import Settings


def test_normalize_contract_payload_populates_route_sections_warnings_and_metadata():
    payload = normalize_contract_payload(
        {
            "answer": "Hello",
            "answer_sections": [{"key": "answer", "title": "Answer", "content": "Hello", "bullets": []}],
            "route_label": "fast_message",
            "uncertainty_box": {"messages": ["Careful with this."]},
            "request_id": "req_1",
            "fast_path": True,
        }
    )

    assert payload["route"] == "fast_message"
    assert len(payload["sections"]) == 1
    assert payload["warnings"] == ["Careful with this."]
    assert payload["metadata"]["request_id"] == "req_1"
    assert payload["metadata"]["fast_path"] is True


def test_timeout_payload_is_contract_safe():
    payload = build_timeout_payload(
        request_id="req_timeout",
        elapsed_ms=1234.5,
        partial_result="Partial answer",
        route="standard_task",
    )

    response = AgentResponse.model_validate(payload)
    assert response.answer == "Partial answer"
    assert response.route == "standard_task"
    assert response.error == "TIME_BUDGET_EXCEEDED"
    assert "timeout" in " ".join(response.warnings).lower()


def test_clarification_payload_is_contract_safe():
    payload = build_clarification_payload(
        request_id="req_clarify",
        query="this one",
    )

    response = AgentResponse.model_validate(payload)
    assert response.route == "standard_task"
    assert response.confidence > 0.0
    assert response.metadata["fallback_reason"] == "clarification"


def test_environment_validation_flags_missing_required_runtime_keys():
    settings = Settings(
        OPENROUTER_API_KEY="",
        SERPER_API_KEY="",
        STORAGE_BACKEND="memory",
    )
    report = validate_runtime_environment(settings)
    assert report["ok"] is False
    assert any("OPENROUTER_API_KEY" in item for item in report["errors"])
    assert any("SERPER_API_KEY" in item for item in report["warnings"])
