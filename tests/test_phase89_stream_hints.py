from __future__ import annotations

from taos.apps.api.routes.agent import _infer_stream_route_hint, _preliminary_line_for_route, _progress_label_for_route


def test_infer_stream_route_hint_deep_research():
    label = _infer_stream_route_hint("latest OpenAI policy update", doc_context_active=False)
    assert label == "deep_research"


def test_infer_stream_route_hint_profile_lookup_as_deep_research():
    label = _infer_stream_route_hint("can u tell me who is the ceo of relyce infotech", doc_context_active=False)
    assert label == "deep_research"


def test_infer_stream_route_hint_doc_mode_from_query():
    label = _infer_stream_route_hint("from this pdf summarize unit 2", doc_context_active=False)
    assert label == "doc_mode"


def test_infer_stream_route_hint_doc_mode_from_active_context_followup():
    label = _infer_stream_route_hint("next", doc_context_active=True)
    assert label == "doc_mode"


def test_preliminary_line_for_route_contains_quick_take():
    line = _preliminary_line_for_route("latest election results", "deep_research")
    assert line.lower().startswith("quick take:")
    assert "evidence" in line.lower()


def test_preliminary_line_for_fast_search_mentions_latest_answer():
    line = _preliminary_line_for_route("current vite version", "fast_search")
    assert line.lower().startswith("quick check:")
    assert "latest answer" in line.lower()


def test_progress_label_for_official_search_mentions_official_sources():
    label = _progress_label_for_route("official_search")
    assert "official sources" in label.lower()
