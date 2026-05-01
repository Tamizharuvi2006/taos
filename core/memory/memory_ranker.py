from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List

from .memory_index import MemoryIndexItem


SOURCE_PRIORITY = {
    "project_memory": 0.20,
    "saved_memory": 0.16,
    "chat_summary": 0.10,
    "retrieved_context": 0.05,
}


@dataclass(frozen=True)
class RankedMemory:
    item: MemoryIndexItem
    score: float
    reason: str


def rank_memories(items: Iterable[MemoryIndexItem], query: str, *, explicit_reference: bool = False) -> List[RankedMemory]:
    query_terms = _terms(query)
    ranked: List[RankedMemory] = []
    for item in items:
        terms = _terms(" ".join([item.content, " ".join(str(v) for v in item.metadata.values())]))
        overlap = len(query_terms & terms)
        if query_terms and overlap <= 0:
            continue
        lexical = overlap / max(1, len(query_terms | terms)) if query_terms else 0.1
        score = lexical + SOURCE_PRIORITY.get(item.source_type, 0.0) + item.confidence * 0.08
        if explicit_reference:
            score += 0.12
        ranked.append(RankedMemory(item=item, score=round(score, 4), reason=f"lexical_overlap:{overlap}"))
    ranked.sort(key=lambda row: row.score, reverse=True)
    return ranked


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9_]+", str(text or "").lower()) if len(term) >= 3}
