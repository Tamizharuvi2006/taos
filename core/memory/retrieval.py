"""
TAOS Memory Retrieval — Top-K relevance-based memory retrieval.

Production features:
- Weighted scoring: relevance × confidence + recency bonus
- Tag-based filtering for scoped queries
- Configurable scoring weights
- Deduplication of results
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Set

from taos.config.constants import DEFAULT_RECENCY_WEIGHT, DEFAULT_RELEVANCE_WEIGHT
from taos.core.memory.memory_store import MemoryEntry, MemoryStore


@dataclass
class RetrievalResult:
    """A scored memory retrieval result."""

    entry: MemoryEntry
    score: float
    match_reason: str = ""

    @property
    def key(self) -> str:
        return self.entry.key

    @property
    def value(self) -> Any:
        return self.entry.value


class MemoryRetriever:
    """
    Top-K memory retrieval with weighted scoring.

    Scoring formula:
        score = (relevance_weight × confidence) + (recency_weight × recency_score)

    Where recency_score decays exponentially with age:
        recency_score = exp(-age_seconds / decay_half_life)
    """

    def __init__(
        self,
        store: MemoryStore,
        relevance_weight: float = DEFAULT_RELEVANCE_WEIGHT,
        recency_weight: float = DEFAULT_RECENCY_WEIGHT,
        decay_half_life: float = 300.0,  # 5 minutes
    ) -> None:
        self._store = store
        self._relevance_weight = relevance_weight
        self._recency_weight = recency_weight
        self._decay_half_life = decay_half_life

    def retrieve_top_k(
        self,
        k: int = 5,
        tags: Optional[Set[str]] = None,
        min_confidence: float = 0.0,
        exclude_keys: Optional[Set[str]] = None,
    ) -> List[RetrievalResult]:
        """
        Retrieve top-K memory entries by weighted score.

        Args:
            k: Maximum number of results to return.
            tags: If provided, only entries matching ANY of these tags are considered.
            min_confidence: Minimum confidence threshold for results.
            exclude_keys: Keys to exclude from results.

        Returns:
            List of RetrievalResult sorted by score descending.
        """
        exclude = exclude_keys or set()

        # Collect candidate entries
        if tags:
            candidates: List[MemoryEntry] = []
            seen_keys: Set[str] = set()
            for tag in tags:
                for entry in self._store.get_by_tag(tag):
                    if entry.key not in seen_keys and entry.key not in exclude:
                        candidates.append(entry)
                        seen_keys.add(entry.key)
        else:
            all_keys = self._store.get_all_keys()
            candidates = []
            for key in all_keys:
                if key not in exclude:
                    entry = self._store.get(key)
                    if entry is not None:
                        candidates.append(entry)

        # Filter by confidence
        candidates = [e for e in candidates if e.confidence >= min_confidence]

        # Score and rank
        scored: List[RetrievalResult] = []
        for entry in candidates:
            score = self._compute_score(entry)
            reason = self._explain_score(entry, score)
            scored.append(RetrievalResult(entry=entry, score=score, match_reason=reason))

        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:k]

    def retrieve_by_query(
        self,
        query_tags: Set[str],
        k: int = 5,
        min_confidence: float = 0.0,
    ) -> List[RetrievalResult]:
        """
        Retrieve entries matching query tags, ranked by tag overlap + score.

        Entries matching more tags rank higher.
        """
        all_keys = self._store.get_all_keys()
        scored: List[RetrievalResult] = []

        for key in all_keys:
            entry = self._store.get(key)
            if entry is None or entry.confidence < min_confidence:
                continue

            tag_overlap = len(entry.tags & query_tags)
            if tag_overlap == 0:
                continue

            # Boost score by tag overlap ratio
            base_score = self._compute_score(entry)
            overlap_bonus = tag_overlap / max(len(query_tags), 1)
            final_score = base_score * (1 + overlap_bonus)

            scored.append(RetrievalResult(
                entry=entry,
                score=final_score,
                match_reason=f"Matched {tag_overlap}/{len(query_tags)} tags",
            ))

        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:k]

    def retrieve_step_context(
        self,
        step_id: str,
        k: int = 5,
    ) -> List[RetrievalResult]:
        """Retrieve all memory relevant to a specific step."""
        return self.retrieve_top_k(
            k=k,
            tags={f"step_{step_id}", "step_result", "reflection"},
        )

    def retrieve_tool_history(
        self,
        tool_name: str,
        k: int = 10,
    ) -> List[RetrievalResult]:
        """Retrieve past outputs from a specific tool."""
        return self.retrieve_top_k(
            k=k,
            tags={f"tool:{tool_name}", "tool_output"},
        )

    def retrieve_failures(self, k: int = 5) -> List[RetrievalResult]:
        """Retrieve recent failure entries for error pattern analysis."""
        return self.retrieve_top_k(k=k, tags={"failed"})

    # ─── Scoring ──────────────────────────────────────────

    def _compute_score(self, entry: MemoryEntry) -> float:
        """Compute weighted score for a memory entry."""
        import math

        # Relevance component: confidence as proxy
        relevance = entry.confidence

        # Recency component: exponential decay
        age = entry.age_seconds
        recency = math.exp(-age / self._decay_half_life) if self._decay_half_life > 0 else 0.0

        # Access frequency bonus (small)
        access_bonus = min(entry.access_count * 0.01, 0.1)

        score = (
            self._relevance_weight * relevance
            + self._recency_weight * recency
            + access_bonus
        )

        return round(score, 4)

    def _explain_score(self, entry: MemoryEntry, score: float) -> str:
        """Generate human-readable explanation of score."""
        return (
            f"score={score:.3f} "
            f"(conf={entry.confidence:.2f}, "
            f"age={entry.age_seconds:.0f}s, "
            f"accesses={entry.access_count})"
        )
