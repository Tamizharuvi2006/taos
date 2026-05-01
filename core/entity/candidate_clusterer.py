from __future__ import annotations

from typing import Dict, Iterable, List

from .disambiguation_models import EntityCandidate


class CandidateClusterer:
    def cluster(self, candidates: Iterable[EntityCandidate]) -> Dict[str, List[EntityCandidate]]:
        clusters: Dict[str, List[EntityCandidate]] = {}
        for candidate in candidates:
            key = "|".join(
                [
                    candidate.name.lower().strip(),
                    candidate.location.lower().strip(),
                    candidate.domain.lower().strip(),
                    candidate.industry.lower().strip(),
                ]
            )
            clusters.setdefault(key, []).append(candidate)
        return clusters
