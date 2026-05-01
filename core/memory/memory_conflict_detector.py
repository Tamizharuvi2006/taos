from __future__ import annotations

import re
from typing import Iterable, List

from .memory_conflict_model import MemoryConflict
from .user_memory_model import UserMemory


def detect_memory_conflicts(user_id: str, old_memories: Iterable[UserMemory], new_memories: Iterable[UserMemory]) -> List[MemoryConflict]:
    conflicts: List[MemoryConflict] = []
    for old in old_memories:
        if old.status != "active":
            continue
        for new in new_memories:
            if old.id == new.id:
                continue
            if new.status != "active":
                continue
            conflict_type = _conflict_type(old.content, new.content)
            if not conflict_type:
                continue
            conflicts.append(
                MemoryConflict(
                    user_id=user_id,
                    type=conflict_type,
                    old_memory_id=old.id,
                    new_memory_id=new.id,
                    old_content=old.content,
                    new_content=new.content,
                    suggested_resolution=_suggest(old.content, new.content, conflict_type),
                ).validate()
            )
    return conflicts


def filter_unresolved_conflicted_memories(memories: Iterable[UserMemory], conflicts: Iterable[MemoryConflict]) -> List[UserMemory]:
    blocked = {conflict.old_memory_id for conflict in conflicts if conflict.status == "unresolved"}
    blocked.update(conflict.new_memory_id for conflict in conflicts if conflict.status == "unresolved")
    return [memory for memory in memories if memory.id not in blocked]


def _conflict_type(old: str, new: str) -> str:
    old_l = old.lower()
    new_l = new.lower()
    if _similar(old_l, new_l) > 0.75:
        return "duplicate_memory"
    if ("short" in old_l or "concise" in old_l) and ("detailed" in new_l or "long" in new_l):
        return "preference_conflict"
    if ("detailed" in old_l or "long" in old_l) and ("short" in new_l or "concise" in new_l):
        return "preference_conflict"
    if "professional" in old_l and any(token in new_l for token in ("playful", "friendly", "casual")):
        return "tone_conflict"
    if "phase" in old_l and "phase" in new_l and _phase(old_l) and _phase(new_l) and _phase(old_l) != _phase(new_l):
        return "stale_memory"
    if "preferred name" in old_l and "preferred name" in new_l and old_l != new_l:
        return "identity_conflict"
    return ""


def _suggest(old: str, new: str, conflict_type: str) -> str:
    if conflict_type == "preference_conflict":
        return "Merge: use detailed phase plans for project work and concise answers for simple questions."
    if conflict_type == "duplicate_memory":
        return "Keep one copy and disable the duplicate."
    if conflict_type == "stale_memory":
        return "Mark the older phase memory stale and keep the newer project status."
    return "Review and choose which memory should remain active."


def _similar(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]+", a))
    tb = set(re.findall(r"[a-z0-9]+", b))
    return len(ta & tb) / max(1, len(ta | tb))


def _phase(text: str) -> str:
    match = re.search(r"\bphase\s+([0-9]{2,3}[a-z]?)\b", text, re.I)
    return match.group(1).upper() if match else ""
