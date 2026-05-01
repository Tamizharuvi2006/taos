from __future__ import annotations

import pytest

from taos.core.feedback.feedback_model import UserFeedback
from taos.core.feedback.feedback_store import FeedbackStore
from taos.core.monitoring.ops_dashboard import build_ops_dashboard


def test_user_feedback_model_accepts_required_feedback_types() -> None:
    feedback = UserFeedback(query="india claude block?", feedback_type="didnt_understand", route="deep_search")
    feedback.validate()
    assert feedback.to_dict()["feedback_type"] == "didnt_understand"


def test_feedback_store_counts_feedback_without_auto_learning() -> None:
    store = FeedbackStore()
    store.add(UserFeedback(query="q1", feedback_type="wrong_answer", route="deep_search"))
    store.add(UserFeedback(query="q2", feedback_type="good_answer", route="fast_search"))
    summary = store.summary()
    assert summary["feedback_counts"]["wrong_answer"] == 1
    assert summary["route_counts"]["deep_search"] == 1
    assert summary["automatic_behavior_change"] is False


def test_feedback_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        UserFeedback(query="q", feedback_type="auto_train_model").validate()


def test_ops_dashboard_includes_feedback_counts() -> None:
    dashboard = build_ops_dashboard(
        execution_records=[],
        feedback_records=[
            {"feedback_type": "wrong_answer", "route": "deep_search"},
            {"feedback_type": "didnt_understand", "route": "deep_search"},
        ],
    )
    assert dashboard["feedback"]["total"] == 2
    assert dashboard["feedback"]["feedback_counts"]["wrong_answer"] == 1
    assert dashboard["feedback"]["automatic_behavior_change"] is False
