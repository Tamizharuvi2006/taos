from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .user_memory_model import UserMemory


@dataclass(frozen=True)
class MemoryClaimCheck:
    allowed: bool
    reason: str
    matched_memory_id: str = ""


def verify_memory_claim(claim: str, memories: Iterable[UserMemory]) -> MemoryClaimCheck:
    claim_terms = _terms(claim)
    for memory in memories:
        if not memory.active or not memory.user_visible:
            continue
        if not (memory.source_chat_id or memory.source_message_id or memory.reason_saved):
            continue
        overlap = len(claim_terms & _terms(memory.content))
        if overlap >= max(1, min(3, len(claim_terms))):
            return MemoryClaimCheck(True, "Claim is supported by active attributed memory.", memory.id)
    return MemoryClaimCheck(False, "No active attributed memory supports this claim.")


def _terms(text: str) -> set[str]:
    import re

    return {term for term in re.findall(r"[a-z0-9_]+", str(text or "").lower()) if len(term) >= 3}
