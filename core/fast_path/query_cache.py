"""
TAOS Query Cache Layer (SPEED MODE ⚡).

Intercepts identical stateless queries at Phase 1A to return
instant 0-latency responses, entirely bypassing the multi-agent orchestration.
"""

from __future__ import annotations

import time
import hashlib
from typing import Optional, Dict

class QueryCache:
    """
    Lightning-fast in-memory cache for common stateless queries.
    Stores formatted responses matched to salted query hashes.
    """

    def __init__(self, max_items: int = 500):
        # We store tuples of (formatted_response, intent, timestamp)
        self._cache = QueryCache._global_cache
        self._max_items = max_items

    # Shared across engine instances (cross-request cache boost)
    _global_cache: Dict[str, tuple[str, str, float]] = {}
        
    def _hash_query(self, query: str) -> str:
        """Create a deterministic hash for a clean query."""
        clean = query.strip().lower()
        return hashlib.sha256(clean.encode()).hexdigest()

    def get(self, query: str) -> Optional[tuple[str, str]]:
        """
        Check if we have a recent cached response.
        Returns (response_text, intent) if found, else None.
        """
        key = self._hash_query(query)
        if key in self._cache:
            response, intent, timestamp = self._cache[key]
            
            # Simple TTL logic: Cache expires after 2 hours
            if time.time() - timestamp > (2 * 3600):
                del self._cache[key]
                return None
                
            return response, intent
            
        return None

    def set(self, query: str, response: str, intent: str) -> None:
        """Store a fresh output in the cache."""
        if len(self._cache) >= self._max_items:
            # Drop the oldest entry if full
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][2])
            del self._cache[oldest_key]
            
        key = self._hash_query(query)
        self._cache[key] = (response, intent, time.time())
