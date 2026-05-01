"""
TAOS Memory Store — In-memory key-value store with metadata.

Production features:
- TTL-based expiration
- Confidence-gated writes (min threshold)
- Capacity management with LRU eviction
- Tag-based indexing for efficient retrieval
- Thread-safe operations
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from taos.config.constants import DEFAULT_MEMORY_MIN_CONFIDENCE


# ═══════════════════════════════════════════════════════════
# MEMORY ENTRY
# ═══════════════════════════════════════════════════════════

@dataclass
class MemoryEntry:
    """A single memory entry with metadata."""

    key: str
    value: Any
    confidence: float = 1.0
    source: str = ""  # e.g., "step_3", "planner", "reflection"
    tags: Set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    ttl: Optional[float] = None  # seconds; None = no expiry

    @property
    def is_expired(self) -> bool:
        if self.ttl is None:
            return False
        return (time.time() - self.created_at) > self.ttl

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    def touch(self) -> None:
        """Update access timestamp and counter."""
        self.last_accessed = time.time()
        self.access_count += 1


# ═══════════════════════════════════════════════════════════
# MEMORY STORE
# ═══════════════════════════════════════════════════════════

class MemoryStore:
    """
    In-memory key-value store with metadata and LRU eviction.

    Thread-safe. All writes go through confidence gating.
    Expired entries are lazily evicted on access or compaction.
    """

    def __init__(
        self,
        capacity: int = 512,
        min_confidence: float = DEFAULT_MEMORY_MIN_CONFIDENCE,
        default_ttl: Optional[float] = None,
    ) -> None:
        self._store: Dict[str, MemoryEntry] = {}
        self._tag_index: Dict[str, Set[str]] = {}  # tag → set of keys
        self._capacity = capacity
        self._min_confidence = min_confidence
        self._default_ttl = default_ttl
        self._lock = threading.Lock()
        self._stats = _StoreStats()

    # ─── Write ────────────────────────────────────────────

    def put(
        self,
        key: str,
        value: Any,
        confidence: float = 1.0,
        source: str = "",
        tags: Optional[Set[str]] = None,
        ttl: Optional[float] = None,
    ) -> bool:
        """
        Store a memory entry.

        Returns True if stored, False if rejected (low confidence or capacity).
        Overwrites existing entries with the same key.
        """
        if confidence < self._min_confidence:
            self._stats.rejected += 1
            return False

        with self._lock:
            # Evict if at capacity and key is new
            if key not in self._store and len(self._store) >= self._capacity:
                self._evict_lru()

            entry = MemoryEntry(
                key=key,
                value=value,
                confidence=confidence,
                source=source,
                tags=tags or set(),
                ttl=ttl or self._default_ttl,
            )

            # Remove old tag index entries if overwriting
            if key in self._store:
                old_entry = self._store[key]
                for tag in old_entry.tags:
                    if tag in self._tag_index:
                        self._tag_index[tag].discard(key)

            self._store[key] = entry

            # Update tag index
            for tag in entry.tags:
                if tag not in self._tag_index:
                    self._tag_index[tag] = set()
                self._tag_index[tag].add(key)

            self._stats.writes += 1
            return True

    # ─── Read ─────────────────────────────────────────────

    def get(self, key: str) -> Optional[MemoryEntry]:
        """Retrieve a memory entry by key. Returns None if not found or expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._stats.misses += 1
                return None

            if entry.is_expired:
                self._remove_entry(key)
                self._stats.misses += 1
                self._stats.expirations += 1
                return None

            entry.touch()
            self._stats.hits += 1
            return entry

    def get_value(self, key: str, default: Any = None) -> Any:
        """Convenience: retrieve just the value."""
        entry = self.get(key)
        return entry.value if entry is not None else default

    def has(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        entry = self.get(key)
        return entry is not None

    # ─── Query ────────────────────────────────────────────

    def get_by_tag(self, tag: str) -> List[MemoryEntry]:
        """Retrieve all non-expired entries with a given tag."""
        with self._lock:
            keys = self._tag_index.get(tag, set()).copy()

        results = []
        for key in keys:
            entry = self.get(key)
            if entry is not None:
                results.append(entry)
        return results

    def get_by_source(self, source: str) -> List[MemoryEntry]:
        """Retrieve all non-expired entries from a specific source."""
        with self._lock:
            results = []
            expired_keys = []
            for key, entry in self._store.items():
                if entry.is_expired:
                    expired_keys.append(key)
                    continue
                if entry.source == source:
                    entry.touch()
                    results.append(entry)

            for key in expired_keys:
                self._remove_entry(key)
                self._stats.expirations += 1

            return results

    def get_recent(self, n: int = 10) -> List[MemoryEntry]:
        """Retrieve the N most recently created entries."""
        with self._lock:
            valid = [e for e in self._store.values() if not e.is_expired]
            valid.sort(key=lambda e: e.created_at, reverse=True)
            for entry in valid[:n]:
                entry.touch()
            return valid[:n]

    def get_all_keys(self) -> List[str]:
        """Return all non-expired keys."""
        with self._lock:
            return [k for k, v in self._store.items() if not v.is_expired]

    # ─── Delete ───────────────────────────────────────────

    def delete(self, key: str) -> bool:
        """Delete a specific entry. Returns True if existed."""
        with self._lock:
            if key in self._store:
                self._remove_entry(key)
                return True
            return False

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._store.clear()
            self._tag_index.clear()

    # ─── Maintenance ──────────────────────────────────────

    def compact(self) -> int:
        """Remove all expired entries. Returns count removed."""
        with self._lock:
            expired_keys = [k for k, v in self._store.items() if v.is_expired]
            for key in expired_keys:
                self._remove_entry(key)
            self._stats.expirations += len(expired_keys)
            return len(expired_keys)

    # ─── Stats ────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "size": self.size,
            "capacity": self._capacity,
            "hits": self._stats.hits,
            "misses": self._stats.misses,
            "writes": self._stats.writes,
            "rejected": self._stats.rejected,
            "evictions": self._stats.evictions,
            "expirations": self._stats.expirations,
        }

    def to_summary(self) -> str:
        """Human-readable summary for logging."""
        s = self.stats
        hit_rate = (s["hits"] / max(s["hits"] + s["misses"], 1)) * 100
        return (
            f"MemoryStore: {s['size']}/{s['capacity']} entries, "
            f"hit_rate={hit_rate:.1f}%, "
            f"writes={s['writes']}, evictions={s['evictions']}"
        )

    # ─── Internal ─────────────────────────────────────────

    def _remove_entry(self, key: str) -> None:
        """Remove an entry and clean up tag index. Caller must hold lock."""
        entry = self._store.pop(key, None)
        if entry:
            for tag in entry.tags:
                if tag in self._tag_index:
                    self._tag_index[tag].discard(key)
                    if not self._tag_index[tag]:
                        del self._tag_index[tag]

    def _evict_lru(self) -> None:
        """Evict the least recently used entry. Caller must hold lock."""
        if not self._store:
            return

        # First try to evict expired entries
        expired_keys = [k for k, v in self._store.items() if v.is_expired]
        if expired_keys:
            self._remove_entry(expired_keys[0])
            self._stats.expirations += 1
            return

        # Otherwise evict LRU
        lru_key = min(self._store, key=lambda k: self._store[k].last_accessed)
        self._remove_entry(lru_key)
        self._stats.evictions += 1


@dataclass
class _StoreStats:
    """Internal stats tracker."""

    hits: int = 0
    misses: int = 0
    writes: int = 0
    rejected: int = 0
    evictions: int = 0
    expirations: int = 0
