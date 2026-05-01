from __future__ import annotations

from typing import Iterable, List

from .entity_models import EntityProfileCandidate


class ProfileDiscovery:
    def rank_profiles(self, candidates: Iterable[EntityProfileCandidate]) -> List[EntityProfileCandidate]:
        safe = [
            candidate for candidate in candidates
            if not candidate.requires_login and not candidate.is_private
        ]
        ranked = [self._with_score(candidate) for candidate in safe]
        return sorted(ranked, key=lambda item: item.confidence, reverse=True)

    def best(self, candidates: Iterable[EntityProfileCandidate]) -> EntityProfileCandidate | None:
        ranked = self.rank_profiles(candidates)
        return ranked[0] if ranked else None

    def _with_score(self, candidate: EntityProfileCandidate) -> EntityProfileCandidate:
        score = 0.0
        if candidate.linked_from_official_site:
            score += 0.46
        if candidate.evidence_type == "verified_social_link":
            score += 0.28
        elif candidate.evidence_type == "official_website":
            score += 0.24
        elif candidate.evidence_type == "company_linkedin":
            score += 0.2
        elif candidate.evidence_type == "random_social":
            score += 0.06
        score += min(0.22, max(0.0, candidate.name_match) * 0.22)
        if candidate.domain_match:
            score += 0.12
        score = round(min(1.0, score), 3)
        if score >= 0.82:
            status = "profile_likely_official"
        elif score >= 0.55:
            status = "profile_unverified"
        else:
            status = "profile_unverified"
        return EntityProfileCandidate(
            handle=candidate.handle,
            platform=candidate.platform,
            url=candidate.url,
            evidence_type=candidate.evidence_type,
            name_match=candidate.name_match,
            domain_match=candidate.domain_match,
            linked_from_official_site=candidate.linked_from_official_site,
            requires_login=candidate.requires_login,
            is_private=candidate.is_private,
            confidence=score,
            status=status,
        )
