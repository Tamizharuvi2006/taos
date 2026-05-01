"""
TAOS Persistence — Abstract Storage Interface.

Defines the contract for all storage backends.
Implementations: FirestoreStore, InMemoryStore (fallback).

All data is scoped by user_id for multi-tenant support.

Collections:
- tasks/{user_id}/items/{task_id}
- executions/{user_id}/items/{execution_id}
- memory/{user_id}/items/{key}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class StorageBackend(ABC):
    """Abstract storage backend interface."""

    @abstractmethod
    async def get(self, collection: str, doc_id: str, user_id: str = "default") -> Optional[Dict[str, Any]]:
        """Get a document by ID."""
        ...

    @abstractmethod
    async def set(self, collection: str, doc_id: str, data: Dict[str, Any], user_id: str = "default") -> None:
        """Create or update a document."""
        ...

    @abstractmethod
    async def delete(self, collection: str, doc_id: str, user_id: str = "default") -> bool:
        """Delete a document."""
        ...

    @abstractmethod
    async def list(
        self,
        collection: str,
        user_id: str = "default",
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List documents in a collection."""
        ...

    @abstractmethod
    async def update(self, collection: str, doc_id: str, updates: Dict[str, Any], user_id: str = "default") -> None:
        """Partially update a document."""
        ...

    @abstractmethod
    async def append_to_list(
        self, collection: str, doc_id: str, field: str, value: Any, user_id: str = "default"
    ) -> None:
        """Append a value to a list field in a document."""
        ...


class InMemoryStore(StorageBackend):
    """
    In-memory storage backend (fallback when Firebase is unavailable).

    Data structure: {collection: {user_id: {doc_id: data}}}
    """

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}

    async def get(self, collection: str, doc_id: str, user_id: str = "default") -> Optional[Dict[str, Any]]:
        return self._store.get(collection, {}).get(user_id, {}).get(doc_id)

    async def set(self, collection: str, doc_id: str, data: Dict[str, Any], user_id: str = "default") -> None:
        self._store.setdefault(collection, {}).setdefault(user_id, {})[doc_id] = data

    async def delete(self, collection: str, doc_id: str, user_id: str = "default") -> bool:
        try:
            del self._store[collection][user_id][doc_id]
            return True
        except KeyError:
            return False

    async def list(
        self,
        collection: str,
        user_id: str = "default",
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        docs = list(self._store.get(collection, {}).get(user_id, {}).values())

        if filters:
            for key, value in filters.items():
                docs = [d for d in docs if d.get(key) == value]

        return docs[:limit]

    async def update(self, collection: str, doc_id: str, updates: Dict[str, Any], user_id: str = "default") -> None:
        doc = self._store.get(collection, {}).get(user_id, {}).get(doc_id)
        if doc:
            doc.update(updates)

    async def append_to_list(
        self, collection: str, doc_id: str, field: str, value: Any, user_id: str = "default"
    ) -> None:
        doc = self._store.get(collection, {}).get(user_id, {}).get(doc_id)
        if doc:
            if field not in doc:
                doc[field] = []
            doc[field].append(value)
