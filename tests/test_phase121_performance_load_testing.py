from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from taos.scripts.run_performance_load_test import (
    PerformanceCase,
    calculate_percentiles,
    load_cases,
    run_load_test,
    write_report,
)


def test_performance_case_loader_works() -> None:
    cases = load_cases(Path("qa/performance_cases.json"))

    assert len(cases) >= 4
    assert {"fast_message", "package_vite_cached", "no_search_explain", "official_search"}.issubset({case.id for case in cases})


def test_mock_load_run_produces_json_report() -> None:
    report = run_load_test(cases=load_cases(Path("qa/performance_cases.json")), live=False)

    assert report["mode"] == "mock"
    assert report["total_requests"] > 0
    assert isinstance(report["routes"], dict)
    assert "latency" in report
    assert "route_budget_matrix" in report
    assert "entity_lookup" in report["route_budget_matrix"]


def _sandbox_dir() -> Path:
    root = Path("tmp_phase121_test_artifacts") / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_markdown_report_is_generated() -> None:
    report = run_load_test(cases=load_cases(Path("qa/performance_cases.json")), live=False, max_cases=2)
    out_root = _sandbox_dir()
    out_json = out_root / "perf.json"
    out_md = out_root / "perf.md"

    write_report(report, json_path=out_json, md_path=out_md)

    assert out_json.exists()
    assert out_md.exists()
    assert "Performance Load Test" in out_md.read_text(encoding="utf-8")
    loaded = json.loads(out_json.read_text(encoding="utf-8"))
    assert loaded["mode"] == "mock"


def test_p50_p90_p95_calculation_works() -> None:
    p = calculate_percentiles([100, 200, 300, 400])

    assert p["p50_ms"] == 250
    assert p["p90_ms"] == 370
    assert p["p95_ms"] == 385


def test_route_latency_budget_failures_are_detected() -> None:
    case = PerformanceCase(
        id="fast_message_tight_budget",
        query="hey buddy",
        expected_route="fast_message",
        concurrency=2,
        max_p95_ms=100,
        request_count=5,
    )
    report = run_load_test(cases=[case], live=False)

    assert report["ok"] is False
    assert len(report["budget_failures"]) == 1
    assert report["budget_failures"][0]["id"] == "fast_message_tight_budget"


def test_concurrency_setting_is_respected_in_mock_mode() -> None:
    case = PerformanceCase(
        id="fast_message_concurrency",
        query="hey buddy",
        expected_route="fast_message",
        concurrency=2,
        max_p95_ms=2000,
        request_count=4,
    )
    report = run_load_test(cases=[case], live=False, concurrency_override=7)

    assert report["cases"][0]["concurrency_used"] == 7
    assert report["cases"][0]["concurrency_requested"] == 7


def test_route_metrics_include_owner_and_cost_fields() -> None:
    report = run_load_test(cases=load_cases(Path("qa/performance_cases.json")), live=False, max_cases=3)

    route_row = report["routes"]["fast_message"]
    assert route_row["owner"] == "direct"
    assert "avg_estimated_cost_usd" in route_row
    assert "p50_ms" in route_row


def test_live_mode_requires_explicit_live_guard() -> None:
    case = PerformanceCase(
        id="live_guard",
        query="hey buddy",
        expected_route="fast_message",
        concurrency=1,
        max_p95_ms=1000,
        request_count=1,
    )
    with pytest.raises(ValueError):
        run_load_test(cases=[case], live=True, explicit_live=False)
