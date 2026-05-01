"""In-memory observability metrics for dashboard and analytics."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class ObservabilityMetrics:
    total_requests: int = 0
    success_requests: int = 0
    failed_requests: int = 0
    total_latency_ms: float = 0.0
    phase_latency_ms: Dict[str, float] = field(default_factory=lambda: defaultdict(float))
    phase_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    agent_usage: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    agent_failures: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    debate_runs: int = 0
    critic_failures: int = 0
    cache_hits: int = 0
    message_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tool_learning_overrides: int = 0
    plan_memory_hits: int = 0
    plan_memory_records: int = 0
    tool_stats_snapshot: Dict[str, Dict[str, float]] = field(default_factory=dict)
    agent_trust_snapshot: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def snapshot(self) -> Dict[str, Any]:
        avg_latency = self.total_latency_ms / self.total_requests if self.total_requests else 0.0
        failure_rate = self.failed_requests / self.total_requests if self.total_requests else 0.0
        return {
            "requests": {
                "total": self.total_requests,
                "success": self.success_requests,
                "failed": self.failed_requests,
                "failure_rate": round(failure_rate, 4),
                "avg_latency_ms": round(avg_latency, 2),
                "cache_hits": self.cache_hits,
            },
            "agents": {
                "usage": dict(self.agent_usage),
                "failures": dict(self.agent_failures),
                "failure_rate_by_agent": {
                    agent: round(self.agent_failures.get(agent, 0) / max(1, count), 4)
                    for agent, count in self.agent_usage.items()
                },
            },
            "debate": {
                "runs": self.debate_runs,
            },
            "critic": {
                "failures": self.critic_failures,
            },
            "messages": dict(self.message_counts),
            "learning": {
                "tool_overrides": self.tool_learning_overrides,
                "plan_memory_hits": self.plan_memory_hits,
                "plan_memory_records": self.plan_memory_records,
                "tool_stats": self.tool_stats_snapshot,
                "agent_trust": self.agent_trust_snapshot,
            },
            "phases": {
                "latency_ms": {k: round(v, 2) for k, v in self.phase_latency_ms.items()},
                "counts": dict(self.phase_counts),
            },
            "generated_at": time.time(),
        }


_METRICS = ObservabilityMetrics()


def get_metrics() -> ObservabilityMetrics:
    return _METRICS
