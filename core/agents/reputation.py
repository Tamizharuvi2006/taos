"""Agent reputation tracking for trust-aware routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class AgentReputation:
    agent_name: str
    success_count: int = 0
    failure_count: int = 0
    total_confidence: float = 0.0
    total_latency_ms: float = 0.0
    observations: int = 0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total else 0.5

    @property
    def avg_confidence(self) -> float:
        return self.total_confidence / self.observations if self.observations else 0.5

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.observations if self.observations else 0.0

    @property
    def trust_score(self) -> float:
        latency_penalty = min(1.0, self.avg_latency_ms / 5000.0)
        return (
            (self.success_rate * 0.5)
            + (self.avg_confidence * 0.3)
            - (latency_penalty * 0.2)
        )


class AgentReputationStore:
    """In-memory reputation store with deterministic trust scoring."""

    def __init__(self) -> None:
        self._data: Dict[str, AgentReputation] = {}

    def get(self, agent_name: str) -> AgentReputation:
        return self._data.setdefault(agent_name, AgentReputation(agent_name=agent_name))

    def trust(self, agent_name: str) -> float:
        return self.get(agent_name).trust_score

    def record(
        self,
        agent_name: str,
        success: bool,
        confidence: float,
        latency_ms: float,
    ) -> None:
        rep = self.get(agent_name)
        if success:
            rep.success_count += 1
        else:
            rep.failure_count += 1
        rep.observations += 1
        rep.total_confidence += max(0.0, min(1.0, confidence))
        rep.total_latency_ms += max(0.0, latency_ms)

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        return {
            name: {
                "success_rate": round(rep.success_rate, 4),
                "avg_confidence": round(rep.avg_confidence, 4),
                "avg_latency_ms": round(rep.avg_latency_ms, 2),
                "trust_score": round(rep.trust_score, 4),
                "observations": rep.observations,
            }
            for name, rep in self._data.items()
        }
