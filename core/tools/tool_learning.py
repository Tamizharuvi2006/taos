"""Tool learning: track tool performance and provide adaptive hints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class ToolStats:
    tool_name: str
    calls: int = 0
    success: int = 0
    failures: int = 0
    total_latency: float = 0.0
    total_cost: float = 0.0
    total_confidence: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.success / self.calls if self.calls else 0.0

    @property
    def avg_latency(self) -> float:
        return self.total_latency / self.calls if self.calls else 0.0

    @property
    def avg_cost(self) -> float:
        return self.total_cost / self.calls if self.calls else 0.0

    @property
    def confidence_score(self) -> float:
        return self.total_confidence / self.calls if self.calls else 0.0

    @property
    def score(self) -> float:
        # Higher is better: reward success and confidence, penalize latency/failures.
        return (
            (self.success_rate * 1.0)
            + (self.confidence_score * 0.5)
            - (self.avg_latency * 0.05)
            - ((self.failures / max(1, self.calls)) * 0.6)
        )


class ToolLearningStore:
    """In-memory performance registry for adaptive tool decisions."""

    def __init__(self) -> None:
        self._stats: Dict[str, ToolStats] = {}
        self._fallback_map = {
            # When search quality degrades, try generic HTTP path.
            "web_search": "http_request",
            # If direct HTTP calls perform poorly, prefer robust search fallback.
            "http_request": "web_search",
        }

    def record(
        self,
        tool_name: Optional[str],
        success: bool,
        latency: float,
        cost: float,
        confidence: float,
    ) -> None:
        if not tool_name:
            return
        stats = self._stats.setdefault(tool_name, ToolStats(tool_name=tool_name))
        stats.calls += 1
        if success:
            stats.success += 1
        else:
            stats.failures += 1
        stats.total_latency += max(0.0, latency)
        stats.total_cost += max(0.0, cost)
        stats.total_confidence += max(0.0, min(1.0, confidence))

    def get_stats(self, tool_name: str) -> ToolStats:
        return self._stats.setdefault(tool_name, ToolStats(tool_name=tool_name))

    def score(self, tool_name: str) -> float:
        return self.get_stats(tool_name).score

    def should_avoid(self, tool_name: str) -> bool:
        s = self.get_stats(tool_name)
        if s.calls < 5:
            return False
        return s.success_rate < 0.45 or s.score < 0.2

    def suggest_fallback(self, tool_name: str) -> Optional[str]:
        return self._fallback_map.get(tool_name)

    def top_tools(self, limit: int = 3) -> List[Tuple[str, float]]:
        ranked = sorted(
            ((name, stats.score) for name, stats in self._stats.items()),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:limit]

    def weakest_tools(self, limit: int = 3) -> List[Tuple[str, float]]:
        ranked = sorted(
            ((name, stats.score) for name, stats in self._stats.items()),
            key=lambda x: x[1],
        )
        return ranked[:limit]

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        return {
            name: {
                "calls": stats.calls,
                "success_rate": round(stats.success_rate, 4),
                "avg_latency": round(stats.avg_latency, 4),
                "avg_cost": round(stats.avg_cost, 6),
                "confidence_score": round(stats.confidence_score, 4),
                "score": round(stats.score, 4),
            }
            for name, stats in self._stats.items()
        }
