from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from .false_memory_guard import MemoryClaimCheck, verify_memory_claim
from .sensitive_memory_filter import check_sensitive_memory
from .user_memory_model import UserMemory


@dataclass(frozen=True)
class MemorySafetyDecision:
    allowed: bool
    reason: str


class MemorySafetyGuard:
    def can_store(self, text: str, *, explicit_user_request: bool = False) -> MemorySafetyDecision:
        result = check_sensitive_memory(text, explicit_user_request=explicit_user_request)
        return MemorySafetyDecision(result.allowed, result.reason)

    def usable_memories(self, memories: Iterable[UserMemory], *, user_id: str) -> List[UserMemory]:
        return [
            memory
            for memory in memories
            if memory.user_id == user_id and memory.active and memory.user_visible
        ]

    def verify_claim(self, claim: str, memories: Iterable[UserMemory]) -> MemoryClaimCheck:
        return verify_memory_claim(claim, memories)

    def assert_user_scope(self, memory: UserMemory, user_id: str) -> None:
        if memory.user_id != user_id:
            raise PermissionError("Cross-user memory access blocked.")
