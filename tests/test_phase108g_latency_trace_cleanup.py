from taos.apps.api.routes.agent import (
    _build_public_trace_summary,
    _build_trace,
    _progress_label_for_route,
    _progress_stages_for_route,
)
from taos.core.reliability.budgeting import (
    build_stage_budget_metadata,
    build_stage_timing_rows,
    summarize_latency,
)


def test_stage_budgets_mark_exceeded_stages():
    budgets = build_stage_budget_metadata("deep_search")
    rows = build_stage_timing_rows(
        {
            "route_ms": 120,
            "search_ms": budgets["search_ms"] + 50,
            "extract_ms": 250,
            "llm_ms": 100,
            "total_ms": 4500,
        },
        "deep_search",
    )

    search_row = next(row for row in rows if row["stage"] == "search")
    assert search_row["budget_exceeded"] is True
    assert summarize_latency({"total_ms": 4500}, rows)["slowest_stage"] == "search"


def test_fast_search_exposes_clean_public_trace():
    trace = _build_trace(
        {
            "route_label": "fast_search",
            "route_decision": {"route": "fast_search", "confidence": 0.93, "reason": "source-of-record lookup"},
            "route_boundary_summary": {"route": "fast_search", "owner": "search_lite", "will_use_planner": False},
            "timing": {"route_ms": 20, "search_ms": 300, "total_ms": 800},
            "trust_block": {"trust_level": "high", "source_count": 1, "answer_mode": "verified"},
        },
        "req_108g_fast",
    )

    assert trace is not None
    assert trace.public_summary["route"]["label"] == "fast_search"
    assert trace.public_summary["route"]["owner"] == "search_lite"
    assert trace.public_summary["execution"]["planner_used"] is False
    assert trace.public_summary["latency"]["total_ms"] == 800
    assert "firebase.initialized" not in str(trace.public_summary).lower()


def test_deep_search_public_trace_includes_research_trust_and_latency():
    summary = _build_public_trace_summary(
        {
            "route_label": "deep_search",
            "planner_path": "research_pipeline",
            "route_decision": {"route": "deep_search", "confidence": 0.88, "reason": "freshness-sensitive research query"},
            "route_boundary_summary": {"route": "deep_search", "owner": "research_pipeline", "will_use_planner": False},
            "timing": {"route_ms": 80, "search_ms": 1500, "extract_ms": 2200, "llm_ms": 900, "total_ms": 5100},
            "evidence_stats": {
                "queries_run": 4,
                "sources_found": 12,
                "usable_sources_count": 4,
                "official_source_count": 1,
                "answer_mode": "best_supported",
                "coverage": 0.82,
            },
            "evidence_matrix_summary": {"coverage": 0.82, "unsupported_claims": 0},
            "freshness_summary": {"freshness_mode": "high"},
            "conflict_summary": {"conflict_detected": False, "groups": []},
            "trust_block": {"trust_level": "medium"},
        }
    )

    assert summary["route"]["owner"] == "research_pipeline"
    assert summary["research"]["queries_run"] == 4
    assert summary["research"]["sources_found"] == 12
    assert summary["research"]["coverage"] == 0.82
    assert summary["research"]["answer_mode"] == "best_supported"
    assert summary["trust"]["unsupported_claims"] == 0
    assert summary["latency"]["slowest_stage"] == "extract"


def test_public_trace_summary_sanitizes_internal_exception_strings():
    summary = _build_public_trace_summary(
        {
            "route_label": "deep_search",
            "route_decision": {"reason": "Traceback Exception: APIKEY leaked stack"},
            "timing": {"total_ms": 10},
        }
    )

    assert summary["route"]["reason"] == "internal detail hidden"
    assert "apikey" not in str(summary).lower()


def test_progress_labels_are_route_specific_and_calm():
    assert _progress_label_for_route("fast_search") == "Checking the best live source..."
    assert _progress_stages_for_route("fast_search") == [
        "Checking the best live source",
        "Verifying the answer",
        "Preparing answer",
    ]
    assert _progress_stages_for_route("official_search")[0] == "Finding official sources"
    assert _progress_stages_for_route("comparison_search")[0] == "Searching both sides"


def test_answer_contract_keeps_warnings_after_answer():
    answer = (
        "Answer\n"
        "The best-supported answer is X.\n\n"
        "Confidence\n"
        "Medium.\n\n"
        "What to treat carefully\n"
        "One source is partial."
    )

    assert answer.index("Answer") < answer.index("What to treat carefully")
