from __future__ import annotations

from .handle_matcher import HandleMatcher
from .social_profile_models import SocialProfileEvidence


class ProfileConfidence:
    def score(self, evidence: SocialProfileEvidence) -> float:
        if evidence.private_or_login_only or evidence.fan_or_unofficial or evidence.unrelated_category:
            return 0.0
        score = 0.0
        if evidence.linked_from_official_site:
            score += 0.42
        if evidence.verified_badge_public:
            score += 0.18
        if evidence.domain_match or evidence.bio_domain_match:
            score += 0.14
        if evidence.same_handle_cross_platform:
            score += 0.08
        if evidence.source_type in {"official_website", "verified_social_link"}:
            score += 0.12
        elif evidence.source_type in {"reputable_article", "company_directory"}:
            score += 0.08
        elif evidence.source_type == "social_search_result":
            score += 0.03
        score += min(0.18, HandleMatcher().score(evidence.entity_name, evidence.handle) * 0.18)
        return round(min(1.0, score), 3)
