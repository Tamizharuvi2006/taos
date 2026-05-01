"""Shared persistence store instance for API/runtime components."""

from __future__ import annotations

from typing import Optional

from taos.config.settings import get_settings
from taos.core.persistence.runtime_status import build_persistence_status
from taos.infra.persistence.firebase_store import FirestoreStore
from taos.infra.persistence.store import InMemoryStore, StorageBackend

_shared_store: Optional[StorageBackend] = None


def get_shared_store() -> StorageBackend:
    """Return a process-wide storage backend instance."""
    global _shared_store
    if _shared_store is not None:
        return _shared_store

    settings = get_settings()
    status = build_persistence_status(settings=settings, check_runtime=True)
    if status.get("production_blocking"):
        raise RuntimeError(f"Persistence is not production-ready: {status.get('fallback_reason') or 'unknown_reason'}")
    if status.get("persistence_mode") == "firestore":
        fb = FirestoreStore()
        _shared_store = fb if fb.is_available else InMemoryStore()
    else:
        _shared_store = InMemoryStore()
    return _shared_store
