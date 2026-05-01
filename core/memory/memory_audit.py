from __future__ import annotations

from typing import Any, Dict, List

from .user_memory_store import UserMemoryStore


def build_memory_audit_report(store: UserMemoryStore, user_id: str) -> Dict[str, Any]:
    memories = store.list(user_id, include_deleted=True, user_visible_only=False)
    events = store.audit(user_id)
    status_counts: Dict[str, int] = {}
    type_counts: Dict[str, int] = {}
    for memory in memories:
        status_counts[memory.status] = status_counts.get(memory.status, 0) + 1
        type_counts[memory.type] = type_counts.get(memory.type, 0) + 1
    return {
        "user_id": user_id,
        "total_memories": len(memories),
        "status_counts": status_counts,
        "type_counts": type_counts,
        "events": events,
        "safe_for_user_display": _safe_events(events),
    }


def _safe_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    safe: List[Dict[str, Any]] = []
    for event in events:
        safe.append(
            {
                "timestamp": event.get("timestamp", ""),
                "action": event.get("action", ""),
                "memory_id": event.get("memory_id", ""),
            }
        )
    return safe
