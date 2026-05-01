"""
Request/stage budgeting helpers for TAOS reliability lock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List


DEFAULT_STAGE_BUDGETS_MS: Dict[str, int] = {
    "route_decision_ms": 300,
    "search_ms": 3000,
    "extract_ms": 7000,
    "evidence_selection_ms": 1000,
    "answer_generation_ms": 5000,
    "finalization_ms": 1000,
}


@dataclass
class RequestBudget:
    total_seconds: float
    deadline_monotonic: float

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - time.monotonic())


class RequestBudgetManager:
    def __init__(self, default_timeout_seconds: float) -> None:
        self._default_timeout_seconds = max(1.0, float(default_timeout_seconds))

    def create_budget(self, override_seconds: float | None = None) -> RequestBudget:
        total = max(1.0, float(override_seconds or self._default_timeout_seconds))
        return RequestBudget(total_seconds=total, deadline_monotonic=time.monotonic() + total)


class StageBudgetManager:
    def __init__(self, reserve_seconds: float = 0.75, minimum_seconds: float = 0.5) -> None:
        self._reserve_seconds = max(0.0, float(reserve_seconds))
        self._minimum_seconds = max(0.1, float(minimum_seconds))

    def allocate(self, budget: RequestBudget, *, preferred_seconds: float | None = None) -> float:
        remaining = budget.remaining_seconds()
        if remaining <= self._minimum_seconds:
            return self._minimum_seconds
        usable = max(self._minimum_seconds, remaining - self._reserve_seconds)
        if preferred_seconds is None:
            return usable
        return max(self._minimum_seconds, min(float(preferred_seconds), usable))


def build_stage_budget_metadata(route_label: str | None = None) -> Dict[str, int]:
    route = str(route_label or "").strip().lower()
    budgets = dict(DEFAULT_STAGE_BUDGETS_MS)
    if route in {"fast_message", "no_search"}:
        budgets.update({"search_ms": 0, "extract_ms": 0, "answer_generation_ms": 1200})
    elif route == "fast_search":
        budgets.update({"search_ms": 2000, "extract_ms": 2500, "answer_generation_ms": 1200})
    elif route in {"deep_search", "deep_research", "news_search", "official_search", "comparison_search"}:
        budgets.update({"search_ms": 4000, "extract_ms": 8000, "answer_generation_ms": 6000})
    return budgets


def build_stage_timing_rows(timing: Dict[str, Any] | None, route_label: str | None = None) -> List[Dict[str, Any]]:
    timing = dict(timing or {})
    budgets = build_stage_budget_metadata(route_label)
    stage_map = [
        ("route_decision", "route_ms", "route_decision_ms"),
        ("search", "search_ms", "search_ms"),
        ("extract", "extract_ms", "extract_ms"),
        ("evidence_selection", "evidence_selection_ms", "evidence_selection_ms"),
        ("answer_generation", "llm_ms", "answer_generation_ms"),
        ("finalization", "finalization_ms", "finalization_ms"),
    ]
    rows: List[Dict[str, Any]] = []
    for stage, timing_key, budget_key in stage_map:
        duration_ms = float(timing.get(timing_key) or 0.0)
        budget_ms = int(budgets.get(budget_key) or 0)
        if duration_ms <= 0.0 and budget_ms <= 0:
            continue
        rows.append(
            {
                "stage": stage,
                "started_at": None,
                "ended_at": None,
                "duration_ms": round(max(0.0, duration_ms), 2),
                "budget_ms": budget_ms,
                "budget_exceeded": bool(budget_ms > 0 and duration_ms > budget_ms),
            }
        )
    total_ms = float(timing.get("total_ms") or timing.get("time_to_final_ms") or 0.0)
    if total_ms > 0.0 and not any(row["stage"] == "total" for row in rows):
        rows.append(
            {
                "stage": "total",
                "started_at": None,
                "ended_at": None,
                "duration_ms": round(total_ms, 2),
                "budget_ms": 0,
                "budget_exceeded": False,
            }
        )
    return rows


def summarize_latency(timing: Dict[str, Any] | None, stage_timings: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    timing = dict(timing or {})
    rows = list(stage_timings or [])
    stage_rows = [row for row in rows if str(row.get("stage")) != "total"]
    slowest = max(stage_rows, key=lambda row: float(row.get("duration_ms") or 0.0), default={})
    return {
        "total_ms": round(float(timing.get("total_ms") or timing.get("time_to_final_ms") or 0.0), 2),
        "slowest_stage": str(slowest.get("stage") or ""),
        "budget_exceeded": any(bool(row.get("budget_exceeded")) for row in rows),
    }
