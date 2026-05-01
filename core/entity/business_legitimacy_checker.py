from __future__ import annotations

from typing import Iterable, List

from .legitimacy_models import LegitimacyEvidence, LegitimacyResult


STRONG = {"official_website", "government_registry", "company_registry", "company_linkedin", "verified_social_link"}
MEDIUM = {"trusted_directory", "reputable_news", "reviews_listing", "directory"}
WEAK = {"seo_listing", "unsourced_directory", "random_social", "search_snippet"}


class BusinessLegitimacyChecker:
    def check(self, evidence_rows: Iterable[LegitimacyEvidence]) -> LegitimacyResult:
        safe = [row for row in evidence_rows if not row.requires_login]
        strong = [row for row in safe if row.source_type in STRONG and row.supports_presence]
        medium = [row for row in safe if row.source_type in MEDIUM and row.supports_presence]
        weak = [row for row in safe if row.source_type in WEAK and row.supports_presence]
        registered = any(row.supports_registration for row in safe if row.source_type in {"government_registry", "company_registry"})
        if strong and registered:
            status = "strong_public_presence"
            confidence = "High"
        elif strong or len(medium) >= 2:
            status = "some_public_presence"
            confidence = "Medium"
        elif medium or weak:
            status = "weak_public_evidence"
            confidence = "Low"
        else:
            status = "not_enough_public_evidence"
            confidence = "Low"
        missing = _missing(strong=strong, registered=registered, medium=medium)
        return LegitimacyResult(
            status=status,
            confidence=confidence,
            evidence_found=tuple(row.title for row in [*strong, *medium, *weak][:6]),
            missing=tuple(missing),
        )


def _missing(*, strong: List[LegitimacyEvidence], registered: bool, medium: List[LegitimacyEvidence]) -> List[str]:
    missing = []
    if not any(row.source_type == "official_website" for row in strong):
        missing.append("official website evidence")
    if not registered:
        missing.append("public registry confirmation")
    if not any(row.source_type == "company_linkedin" for row in strong):
        missing.append("official LinkedIn evidence")
    if not medium:
        missing.append("independent directory/news evidence")
    return missing
