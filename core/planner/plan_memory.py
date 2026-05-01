"""Plan memory store: learn successful/failing plan patterns over time."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from taos.core.state.state_schema import PlanObject


@dataclass
class PlanRecord:
    goal: str
    plan_steps: List[str]
    outcome: str  # success|failed
    confidence: float
    cost: float
    latency_ms: float
    failure_reason: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def score(self) -> float:
        base = 1.0 if self.outcome == "success" else -0.3
        return base + (self.confidence * 0.6) - (self.cost * 0.2) - (self.latency_ms / 20000.0)


class PlanMemoryStore:
    """In-memory plan memory with similarity retrieval."""

    def __init__(self, max_records: int = 1000) -> None:
        self._records: List[PlanRecord] = []
        self._max_records = max_records

    def add_record(
        self,
        goal: str,
        plan: Optional[PlanObject],
        outcome: str,
        confidence: float,
        cost: float,
        latency_ms: float,
        failure_reason: str = "",
    ) -> None:
        steps: List[str] = []
        if plan:
            steps = [f"{s.tool or 'reasoning'}:{s.action}" for s in plan.steps]
        rec = PlanRecord(
            goal=goal,
            plan_steps=steps,
            outcome=outcome,
            confidence=confidence,
            cost=cost,
            latency_ms=latency_ms,
            failure_reason=failure_reason,
        )
        self._records.append(rec)
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records :]

    def retrieve_similar(
        self,
        goal: str,
        top_k: int = 3,
        min_similarity: float = 0.18,
    ) -> List[PlanRecord]:
        if not self._records:
            return []
        q_tokens = self._tokenize(goal)
        scored = []
        for rec in self._records:
            sim = self._jaccard(q_tokens, self._tokenize(rec.goal))
            if sim < min_similarity:
                continue
            scored.append((sim + (rec.score * 0.2), rec))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k]]

    def build_planner_hints(self, records: List[PlanRecord], max_hints: int = 3) -> List[str]:
        hints: List[str] = []
        for rec in records[:max_hints]:
            step_chain = " -> ".join([p.split(":", 1)[0] for p in rec.plan_steps[:5]]) or "reasoning"
            if rec.outcome == "success":
                hints.append(
                    f"[PlanMemory] Similar successful pattern: {step_chain} (confidence={rec.confidence:.2f}, cost={rec.cost:.3f})."
                )
            else:
                hints.append(
                    f"[PlanMemory] Similar failed pattern to avoid: {step_chain}. Failure reason: {rec.failure_reason or 'low confidence'}."
                )
        return hints

    @property
    def count(self) -> int:
        return len(self._records)

    def _tokenize(self, text: str) -> set[str]:
        toks = re.findall(r"[a-zA-Z0-9]+", text.lower())
        stop = {"the", "is", "a", "an", "and", "or", "to", "of", "in", "for", "with", "on"}
        return {t for t in toks if len(t) > 2 and t not in stop}

    def _jaccard(self, a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / max(1, len(a | b))
