from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class LiveEvalGuard:
    max_cases: int = 8
    max_cost_estimate: float = 1.0
    max_runtime_seconds: float = 300.0
    required_env: List[str] = field(default_factory=lambda: ["SERPER_API_KEY"])

    def __post_init__(self) -> None:
        self.started_at = time.time()
        self.cases_run = 0
        self.cost_estimate = 0.0
        self.skipped_cases: List[Dict[str, Any]] = []

    def readiness(self, *, live_requested: bool = False) -> Dict[str, Any]:
        missing = [name for name in self.required_env if not os.getenv(name)]
        return {
            "ready": not missing,
            "missing_env": missing,
            "provider_available": not missing,
            "live_requested": bool(live_requested),
            "mode": "live" if live_requested else "mock",
        }

    def before_case(self, case_id: str, *, estimated_cost: float = 0.05) -> Dict[str, Any]:
        if self.cases_run >= self.max_cases:
            return self._skip(case_id, "max_cases_exceeded")
        if self.cost_estimate + estimated_cost > self.max_cost_estimate:
            return self._skip(case_id, "max_cost_exceeded")
        if time.time() - self.started_at > self.max_runtime_seconds:
            return self._skip(case_id, "max_runtime_exceeded")
        self.cases_run += 1
        self.cost_estimate += float(estimated_cost or 0.0)
        return {"allowed": True, "reason": "ok"}

    def summary(self) -> Dict[str, Any]:
        return {
            "cases_run": self.cases_run,
            "cost_estimate": round(self.cost_estimate, 4),
            "runtime_seconds": round(time.time() - self.started_at, 3),
            "skipped_cases": list(self.skipped_cases),
            "limits": {
                "max_cases": self.max_cases,
                "max_cost_estimate": self.max_cost_estimate,
                "max_runtime_seconds": self.max_runtime_seconds,
            },
        }

    def skip_case(self, case_id: str, reason: str) -> Dict[str, Any]:
        return self._skip(case_id, reason)

    def _skip(self, case_id: str, reason: str) -> Dict[str, Any]:
        row = {"case_id": str(case_id), "reason": reason}
        self.skipped_cases.append(row)
        return {"allowed": False, **row}
