from __future__ import annotations

from typing import Iterable

from .profile_confidence import ProfileConfidence
from .social_profile_models import SocialProfileEvidence, SocialProfileVerification


class SocialProfileVerifier:
    def verify(self, evidence_rows: Iterable[SocialProfileEvidence]) -> SocialProfileVerification:
        scorer = ProfileConfidence()
        scored = [
            (row, scorer.score(row))
            for row in evidence_rows
            if not (row.private_or_login_only or row.fan_or_unofficial or row.unrelated_category)
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        if not scored:
            return SocialProfileVerification(
                mode="no_public_profile_found",
                reason="No safe public profile evidence remained after filtering private/login-only or unrelated rows.",
            )
        top, score = scored[0]
        candidates = tuple(f"{row.handle} ({confidence:.2f})" for row, confidence in scored[:4])
        if len(scored) > 1 and abs(score - scored[1][1]) < 0.08:
            return SocialProfileVerification(
                mode="multiple_profile_candidates",
                handle=top.handle,
                platform=top.platform,
                confidence=score,
                reason="Multiple public profile candidates are close in confidence.",
                candidates=candidates,
            )
        if top.linked_from_official_site and score >= 0.82:
            mode = "official_profile_verified"
        elif score >= 0.66:
            mode = "likely_official_profile"
        else:
            mode = "possible_profile_unverified"
        return SocialProfileVerification(
            mode=mode,
            handle=top.handle,
            platform=top.platform,
            confidence=score,
            reason="Ranked by official link, public verification, handle/name match, and domain consistency.",
            candidates=candidates,
        )
