from __future__ import annotations

from .memory_space_model import MemorySpace


READ_ROLES = {"owner", "admin", "editor", "viewer"}
EDIT_ROLES = {"owner", "admin", "editor"}
ADMIN_ROLES = {"owner", "admin"}


def role_for(space: MemorySpace, user_id: str) -> str:
    return space.members.get(user_id, "")


def can_read(space: MemorySpace, user_id: str) -> bool:
    return role_for(space, user_id) in READ_ROLES


def can_edit(space: MemorySpace, user_id: str) -> bool:
    return role_for(space, user_id) in EDIT_ROLES


def can_admin(space: MemorySpace, user_id: str) -> bool:
    return role_for(space, user_id) in ADMIN_ROLES


def require_read(space: MemorySpace, user_id: str) -> None:
    if not can_read(space, user_id):
        raise PermissionError("User cannot read this memory space.")


def require_edit(space: MemorySpace, user_id: str) -> None:
    if not can_edit(space, user_id):
        raise PermissionError("User cannot edit this memory space.")


def require_admin(space: MemorySpace, user_id: str) -> None:
    if not can_admin(space, user_id):
        raise PermissionError("User cannot manage this memory space.")
