from __future__ import annotations

from difflib import SequenceMatcher
from typing import Dict, Iterable

from .disambiguation_models import DisambiguationResult, EntityCandidate


class EntityDisambiguator:
    def disambiguate(self, *, query: str, candidates: Iterable[EntityCandidate]) -> DisambiguationResult:
        rows = list(candidates)
        if not rows:
            return DisambiguationResult(mode="clarification_needed", reason="No public candidates were supplied.")
        scored = [(candidate, self._score(query, candidate)) for candidate in rows]
        scored.sort(key=lambda item: item[1], reverse=True)
        top, top_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        if len(scored) > 1 and top_score - second_score < 0.16:
            return DisambiguationResult(
                mode="clarification_needed",
                candidates=tuple(candidate for candidate, _ in scored),
                confidence=round(top_score, 3),
                reason="Multiple candidates are too close; ask the user for location, website, industry, or handle.",
            )
        return DisambiguationResult(
            mode="selected_candidate",
            selected=top,
            candidates=tuple(candidate for candidate, _ in scored),
            confidence=round(top_score, 3),
            reason="Selected candidate has the strongest match to query context and source agreement.",
        )

    def _score(self, query: str, candidate: EntityCandidate) -> float:
        lower = str(query or "").lower()
        score = SequenceMatcher(None, _compact(candidate.name), _compact(lower)).ratio() * 0.42
        if candidate.location and candidate.location.lower() in lower:
            score += 0.22
        if candidate.domain and candidate.domain.lower() in lower:
            score += 0.22
        if candidate.industry and candidate.industry.lower() in lower:
            score += 0.12
        if candidate.social_handle and candidate.social_handle.lower().lstrip("@") in lower:
            score += 0.16
        score += min(0.16, max(0, candidate.source_count) * 0.04)
        if candidate.domain:
            compact_domain = _compact(candidate.domain)
            if compact_domain and compact_domain in _compact(lower):
                score += 0.12
        return min(1.0, score)


def selected_evidence(candidate: EntityCandidate) -> Dict[str, str]:
    return dict(candidate.evidence)


def _compact(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())
