from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


def normalize_query(query: str) -> str:
    """Normalize a query for exact route-cache lookups."""
    text = str(query or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[?!.,;:]+$", "", text)
    return text


_ROUTE_TTLS = {
    "fast_message": 24 * 3600,
    "no_search": 24 * 3600,
    "fast_search": 3600,
    "news_search": 10 * 60,
    "deep_search": 6 * 3600,
    "official_search": 3600,
    "comparison_search": 6 * 3600,
    "doc_mode": 30 * 60,
    "task": 5 * 60,
    "clarification": 0,
}


@dataclass
class CacheEntry:
    payload: Dict[str, Any]
    created_at: float
    ttl_seconds: int

    def is_stale(self, now: Optional[float] = None) -> bool:
        if self.ttl_seconds <= 0:
            return True
        return (now or time.time()) - self.created_at > self.ttl_seconds


class RouteCache:
    """Small in-memory exact route cache with route-specific TTLs."""

    _global_cache: Dict[str, CacheEntry] = {}

    def __init__(self, max_items: int = 500) -> None:
        self._cache = RouteCache._global_cache
        self._max_items = max(10, int(max_items or 500))

    def get(self, normalized_query: str) -> tuple[Optional[Dict[str, Any]], str]:
        key = normalize_query(normalized_query)
        entry = self._cache.get(key)
        if not entry:
            return None, "miss"
        if entry.is_stale():
            return None, "stale"
        return dict(entry.payload), "hit"

    def set(self, normalized_query: str, payload: Dict[str, Any]) -> None:
        route = str((payload or {}).get("route") or "").strip()
        ttl = int(_ROUTE_TTLS.get(route, 0))
        if ttl <= 0:
            return
        if route == "clarification" and bool((payload or {}).get("high_stakes")):
            return
        if len(self._cache) >= self._max_items:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].created_at)
            self._cache.pop(oldest_key, None)
        self._cache[normalize_query(normalized_query)] = CacheEntry(
            payload=dict(payload),
            created_at=time.time(),
            ttl_seconds=ttl,
        )

    def clear(self) -> None:
        self._cache.clear()
