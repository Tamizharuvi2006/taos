from __future__ import annotations

from pathlib import Path

from scripts.build_post_release_report import build_report, render_markdown


def test_post_release_monitoring_doc_exists_and_mentions_required_signals() -> None:
    text = Path("docs/POST_RELEASE_MONITORING.md").read_text(encoding="utf-8")
    assert "Unknown route count" in text
    assert "Bad user feedback" in text
    assert "Document QA failures" in text


def test_post_release_report_groups_bugfix_queue() -> None:
    report = build_report(
        [
            {"route": "deep_search", "feedback_type": "didnt_understand"},
            {"route": "deep_search", "category": "low_coverage"},
            {"route": "admin", "category": "admin_dashboard_errors"},
        ]
    )
    assert report["total_records"] == 3
    assert report["bugfix_queue"][0]["count"] >= 1
    assert any(item["priority"] == "high" for item in report["bugfix_queue"])


def test_post_release_markdown_render() -> None:
    md = render_markdown(build_report([{"route": "unknown"}]))
    assert "Post-release Report" in md
    assert "| Group | Count | Priority |" in md
