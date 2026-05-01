from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urldefrag

TTL_BY_MODE = {
    "news_live": 20 * 60,
    "news_search": 20 * 60,
    "current_lookup": 3 * 3600,
    "fast_search": 3600,
    "general_research": 24 * 3600,
    "deep_search": 24 * 3600,
    "historical": 14 * 24 * 3600,
}


@dataclass
class ExtractCacheRecord:
    value: Dict[str, Any]
    created_at: float
    ttl_seconds: int

    def stale(self) -> bool:
        return time.time() - self.created_at > self.ttl_seconds


class ExtractCache:
    _global: Dict[str, ExtractCacheRecord] = {}

    def __init__(self, ttl_seconds: int = 24 * 3600, max_items: int = 1000) -> None:
        self.ttl_seconds = int(ttl_seconds or 24 * 3600)
        self.max_items = max(10, int(max_items or 1000))
        self._cache = ExtractCache._global

    def canonical_url(self, url: str) -> str:
        clean, _frag = urldefrag(str(url or "").strip())
        return clean.rstrip("/")

    def get(
        self,
        url: str,
        *,
        freshness_mode: str = "general_research",
        allow_stale: bool = False,
    ) -> tuple[Optional[Dict[str, Any]], str]:
        key = self.canonical_url(url)
        record = self._cache.get(key)
        if not record:
            return None, "miss"
        if record.stale():
            if not allow_stale:
                return None, "stale"
            value = dict(record.value)
            value["cache_status"] = "stale"
            value["freshness_mode"] = freshness_mode
            return value, "stale"
        value = dict(record.value)
        value["cache_status"] = "hit"
        value["freshness_mode"] = freshness_mode
        return value, "hit"

    def set(
        self,
        url: str,
        value: Dict[str, Any],
        *,
        freshness_mode: str = "general_research",
    ) -> None:
        key = self.canonical_url(url)
        if not key:
            return
        if len(self._cache) >= self.max_items:
            oldest = min(self._cache, key=lambda item: self._cache[item].created_at)
            self._cache.pop(oldest, None)
        ttl_seconds = int(TTL_BY_MODE.get(freshness_mode, self.ttl_seconds))
        self._cache[key] = ExtractCacheRecord(dict(value or {}), time.time(), ttl_seconds)

    def clear(self) -> None:
        self._cache.clear()
