from __future__ import annotations

from taos.core.entity.handle_matcher import HandleMatcher
from taos.core.entity.profile_confidence import ProfileConfidence
from taos.core.entity.social_profile_models import SocialProfileEvidence
from taos.core.entity.social_profile_verifier import SocialProfileVerifier


def test_official_website_social_link_ranks_highest() -> None:
    result = SocialProfileVerifier().verify(
        [
            SocialProfileEvidence(handle="@random", platform="instagram", entity_name="Relyce Infotech", source_type="social_search_result"),
            SocialProfileEvidence(
                handle="@relyceinfotech",
                platform="instagram",
                entity_name="Relyce Infotech",
                source_type="official_website",
                linked_from_official_site=True,
                domain_match=True,
            ),
        ]
    )
    assert result.mode == "official_profile_verified"
    assert result.handle == "@relyceinfotech"


def test_random_social_profile_is_not_official() -> None:
    result = SocialProfileVerifier().verify(
        [SocialProfileEvidence(handle="@relycefan", platform="instagram", entity_name="Relyce Infotech", fan_or_unofficial=True)]
    )
    assert result.mode == "no_public_profile_found"


def test_multiple_candidates_are_separated() -> None:
    result = SocialProfileVerifier().verify(
        [
            SocialProfileEvidence(handle="@nova_labs", platform="instagram", entity_name="Nova Labs", source_type="company_directory", domain_match=True),
            SocialProfileEvidence(handle="@novalabs", platform="instagram", entity_name="Nova Labs", source_type="company_directory", domain_match=True),
        ]
    )
    assert result.mode == "multiple_profile_candidates"
    assert len(result.candidates) == 2


def test_confidence_explanation_uses_public_signals() -> None:
    evidence = SocialProfileEvidence(
        handle="@relyceinfotech",
        platform="instagram",
        entity_name="Relyce Infotech",
        verified_badge_public=True,
        same_handle_cross_platform=True,
    )
    assert ProfileConfidence().score(evidence) > 0.35
    assert HandleMatcher().score("Relyce Infotech", "@relyceinfotech") >= 0.86


def test_private_login_only_content_is_not_used() -> None:
    result = SocialProfileVerifier().verify(
        [SocialProfileEvidence(handle="@private", platform="instagram", entity_name="Relyce Infotech", private_or_login_only=True)]
    )
    assert result.mode == "no_public_profile_found"
