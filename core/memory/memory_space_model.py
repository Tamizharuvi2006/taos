from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict
from uuid import uuid4

from .user_memory_model import UserMemory, utc_now_iso


SCOPES = {"personal", "project", "team", "global_system"}
ROLES = {"owner", "admin", "editor", "viewer"}


def new_space_id() -> str:
    return f"space_{uuid4().hex[:12]}"


@dataclass
class MemorySpace:
    name: str
    owner_user_id: str
    scope: str = "project"
    id: str = field(default_factory=new_space_id)
    members: Dict[str, str] = field(default_factory=dict)
    memories: Dict[str, UserMemory] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def validate(self) -> "MemorySpace":
        if self.scope not in SCOPES:
            raise ValueError(f"unsupported memory scope: {self.scope}")
        self.members.setdefault(self.owner_user_id, "owner")
        for role in self.members.values():
            if role not in ROLES:
                raise ValueError(f"unsupported memory role: {role}")
        return self

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["memories"] = [memory.to_dict() for memory in self.memories.values() if memory.status != "deleted"]
        return data
