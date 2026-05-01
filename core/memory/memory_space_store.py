from __future__ import annotations

from threading import RLock
from typing import Dict, List

from .memory_space_model import MemorySpace
from .shared_memory_permissions import require_admin, require_edit, require_read
from .user_memory_model import UserMemory, utc_now_iso


class MemorySpaceStore:
    def __init__(self) -> None:
        self._spaces: Dict[str, MemorySpace] = {}
        self._lock = RLock()

    def create_space(self, name: str, owner_user_id: str, *, scope: str = "project") -> MemorySpace:
        space = MemorySpace(name=name, owner_user_id=owner_user_id, scope=scope).validate()
        with self._lock:
            self._spaces[space.id] = space
        return space

    def list_spaces(self, user_id: str) -> List[MemorySpace]:
        with self._lock:
            return [space for space in self._spaces.values() if user_id in space.members]

    def get_space(self, space_id: str, user_id: str) -> MemorySpace:
        space = self._spaces.get(space_id)
        if space is None:
            raise KeyError(space_id)
        require_read(space, user_id)
        return space

    def add_memory(self, space_id: str, user_id: str, content: str, *, tags: List[str] | None = None) -> UserMemory:
        space = self._spaces.get(space_id)
        if space is None:
            raise KeyError(space_id)
        require_edit(space, user_id)
        memory = UserMemory(
            user_id=user_id,
            content=content,
            type="project_memory" if space.scope == "project" else "saved_memory",
            source_chat_id=space.id,
            tags=list(tags or ["shared_memory"]),
            reason_saved=f"Added to {space.scope} memory space.",
        ).validate()
        space.memories[memory.id] = memory
        space.updated_at = utc_now_iso()
        return memory

    def update_memory(self, space_id: str, user_id: str, memory_id: str, **changes) -> UserMemory:
        space = self._spaces.get(space_id)
        if space is None:
            raise KeyError(space_id)
        require_edit(space, user_id)
        memory = space.memories.get(memory_id)
        if memory is None:
            raise KeyError(memory_id)
        memory.update(**changes)
        space.updated_at = utc_now_iso()
        return memory

    def delete_memory(self, space_id: str, user_id: str, memory_id: str) -> bool:
        space = self._spaces.get(space_id)
        if space is None:
            raise KeyError(space_id)
        require_edit(space, user_id)
        memory = space.memories.get(memory_id)
        if memory is None:
            return False
        memory.update(status="deleted")
        space.updated_at = utc_now_iso()
        return True

    def add_member(self, space_id: str, actor_user_id: str, target_user_id: str, role: str) -> MemorySpace:
        space = self._spaces.get(space_id)
        if space is None:
            raise KeyError(space_id)
        require_admin(space, actor_user_id)
        space.members[target_user_id] = role
        space.validate()
        space.updated_at = utc_now_iso()
        return space

    def remove_member(self, space_id: str, actor_user_id: str, target_user_id: str) -> MemorySpace:
        space = self._spaces.get(space_id)
        if space is None:
            raise KeyError(space_id)
        require_admin(space, actor_user_id)
        if target_user_id != space.owner_user_id:
            space.members.pop(target_user_id, None)
        space.updated_at = utc_now_iso()
        return space

    def reset(self) -> None:
        with self._lock:
            self._spaces.clear()


GLOBAL_MEMORY_SPACE_STORE = MemorySpaceStore()
