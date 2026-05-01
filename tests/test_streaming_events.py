from __future__ import annotations

from taos.core.performance.progress import ProgressPhase
from taos.core.streaming.events import StreamEvent, StreamEventType, phase_to_event_type


def test_phase_to_event_type_mapping():
    assert phase_to_event_type(ProgressPhase.RECEIVED.value) == StreamEventType.START
    assert phase_to_event_type(ProgressPhase.EXECUTING.value) == StreamEventType.PROGRESS
    assert phase_to_event_type(ProgressPhase.COMPLETE.value) == StreamEventType.FINAL
    assert phase_to_event_type(ProgressPhase.FAILED.value) == StreamEventType.ERROR


def test_stream_event_payload_shape():
    evt = StreamEvent(
        request_id="abc123",
        event_type=StreamEventType.START,
        phase_name="received",
        progress=0,
        message="Query received",
    )
    payload = evt.to_sse_payload()
    assert payload["request_id"] == "abc123"
    assert payload["event_type"] == "START"
