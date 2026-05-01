from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SocialProfileEvidence:
    handle: str
    platform: str
    url: str = ""
    entity_name: str = ""
    source_type: str = "social_search_result"
    linked_from_official_site: bool = False
    verified_badge_public: bool = False
    domain_match: bool = False
    bio_domain_match: bool = False
    same_handle_cross_platform: bool = False
    private_or_login_only: bool = False
    fan_or_unofficial: bool = False
    unrelated_category: bool = False


@dataclass(frozen=True)
class SocialProfileVerification:
    mode: str
    handle: str = ""
    platform: str = ""
    confidence: float = 0.0
    reason: str = ""
    candidates: tuple[str, ...] = ()
