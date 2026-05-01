from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


def _normalize(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


TTL_BY_MODE = {
    "news_live": 20 * 60,
    "news_search": 20 * 60,
    "current_lookup": 15 * 60,
    "fast_search": 3600,
    "general_research": 24 * 3600,
    "deep_search": 24 * 3600,
    "historical": 14 * 24 * 3600,
}


@dataclass
class SearchCacheRecord:
    value: Dict[str, Any]
    created_at: float
    ttl_seconds: int

    def status(self) -> str:
        return "stale" if time.time() - self.created_at > self.ttl_seconds else "hit"


class SearchResultCache:
    _global: Dict[str, SearchCacheRecord] = {}

    def __init__(self, max_items: int = 500, *, shared: bool = False) -> None:
        self._cache = SearchResultCache._global if shared else {}
        self._max_items = max(10, int(max_items or 500))

    def key(self, *, query: str, mode: str, search_type: str = "search") -> str:
        raw = f"{_normalize(query)}|{_normalize(mode)}|{_normalize(search_type)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, *, query: str, mode: str, search_type: str = "search", allow_stale: bool = False) -> tuple[Optional[Dict[str, Any]], str]:
        key = self.key(query=query, mode=mode, search_type=search_type)
        record = self._cache.get(key)
        if not record:
            return None, "miss"
        status = record.status()
        if status == "stale" and not allow_stale:
            return None, "stale"
        value = dict(record.value)
        value.setdefault("metadata", {})
        if isinstance(value["metadata"], dict):
            value["metadata"]["cache_status"] = status
        return value, status

    def set(self, *, query: str, mode: str, value: Dict[str, Any], search_type: str = "search") -> None:
        ttl = int(TTL_BY_MODE.get(mode, TTL_BY_MODE.get(search_type, 3600)))
        if len(self._cache) >= self._max_items:
            oldest = min(self._cache, key=lambda key: self._cache[key].created_at)
            self._cache.pop(oldest, None)
        self._cache[self.key(query=query, mode=mode, search_type=search_type)] = SearchCacheRecord(
            value=dict(value or {}),
            created_at=time.time(),
            ttl_seconds=ttl,
        )

    def clear(self) -> None:
        self._cache.clear()
