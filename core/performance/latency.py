"""
TAOS Latency Optimizer — Reduce response times.

UPGRADE #2: LATENCY OPTIMIZATION

Targets:
- Fast path → <1s
- Normal → <3s

Strategies:
1. Response caching (in-memory LRU)
2. LLM call deduplication
3. Early termination for confident results
4. Parallel tool execution hints
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class LatencyStats:
    """Track latency metrics."""
    total_requests: int = 0
    cache_hits: int = 0
    fast_path_count: int = 0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    _latencies: List[float] = field(default_factory=list)

    def record(self, latency_ms: float, was_cache_hit: bool = False, was_fast_path: bool = False):
        self.total_requests += 1
        if was_cache_hit:
            self.cache_hits += 1
        if was_fast_path:
            self.fast_path_count += 1
        self._latencies.append(latency_ms)
        self.avg_latency_ms = sum(self._latencies) / len(self._latencies)
        sorted_l = sorted(self._latencies)
        idx = int(len(sorted_l) * 0.95)
        self.p95_latency_ms = sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def cache_hit_rate(self) -> float:
        return self.cache_hits / max(1, self.total_requests)


class ResponseCache:
    """
    LRU response cache with TTL.

    Caches full formatted responses keyed by normalized query.
    """

    def __init__(self, capacity: int = 512, ttl_seconds: int = 1800) -> None:
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._capacity = capacity
        self._ttl = ttl_seconds
        self._access_order: List[str] = []

    def get(self, query: str) -> Optional[Dict[str, Any]]:
        key = self._normalize(query)
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() - entry["ts"] > self._ttl:
            del self._cache[key]
            return None
        # Move to end (most recent)
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)
        return entry["data"]

    def put(self, query: str, response: Dict[str, Any]) -> None:
        key = self._normalize(query)
        if len(self._cache) >= self._capacity:
            self._evict_lru()
        self._cache[key] = {"data": response, "ts": time.time()}
        self._access_order.append(key)

    def _evict_lru(self) -> None:
        if self._access_order:
            oldest = self._access_order.pop(0)
            self._cache.pop(oldest, None)

    def _normalize(self, query: str) -> str:
        return hashlib.md5(query.strip().lower().encode()).hexdigest()

    @property
    def size(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()
        self._access_order.clear()


class EarlyTerminator:
    """
    Early termination logic — stop execution when confident enough.

    If after step N the confidence is already very high,
    skip remaining steps and return early.
    """

    def __init__(self, min_confidence: float = 0.9, min_steps: int = 1) -> None:
        self._min_confidence = min_confidence
        self._min_steps = min_steps

    def should_terminate_early(
        self,
        step_index: int,
        total_steps: int,
        current_confidence: float,
        has_result: bool,
    ) -> Tuple[bool, str]:
        """
        Check if we can terminate early.

        Returns (should_terminate, reason).
        """
        if step_index < self._min_steps:
            return False, ""

        if not has_result:
            return False, ""

        # High confidence + at least one step done
        if current_confidence >= self._min_confidence:
            return True, f"High confidence ({current_confidence:.0%}) after step {step_index+1}/{total_steps}"

        # More than half done with good confidence
        if step_index >= total_steps // 2 and current_confidence >= 0.8:
            return True, f"Good confidence ({current_confidence:.0%}) at {step_index+1}/{total_steps}"

        return False, ""


class LatencyOptimizer:
    """
    Central latency optimization manager.

    Combines caching, early termination, and deduplication.
    """

    def __init__(self) -> None:
        self.cache = ResponseCache()
        self.early_terminator = EarlyTerminator()
        self.stats = LatencyStats()

    def check_cache(self, query: str) -> Optional[Dict[str, Any]]:
        """Check if a cached response exists."""
        return self.cache.get(query)

    def cache_response(self, query: str, response: Dict[str, Any]) -> None:
        """Cache a successful response."""
        if response.get("success"):
            self.cache.put(query, response)

    def record_latency(self, latency_ms: float, **kwargs) -> None:
        """Record latency metrics."""
        self.stats.record(latency_ms, **kwargs)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_requests": self.stats.total_requests,
            "cache_hits": self.stats.cache_hits,
            "cache_hit_rate": f"{self.stats.cache_hit_rate:.0%}",
            "fast_path_count": self.stats.fast_path_count,
            "avg_latency_ms": round(self.stats.avg_latency_ms, 1),
            "p95_latency_ms": round(self.stats.p95_latency_ms, 1),
            "cache_size": self.cache.size,
        }
