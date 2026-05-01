from __future__ import annotations

import re

from .entity_models import EntityIntent, EntityQuery
from .entity_resolver import PublicEntityResolver


class EntityIntentDetector:
    def __init__(self) -> None:
        self._resolver = PublicEntityResolver()

    def detect(self, query: str) -> EntityQuery:
        raw = str(query or "").strip()
        lower = raw.lower()
        intent = self._intent(lower)
        resolved = self._resolver.resolve(raw)
        entity_name = resolved["entity_name"]
        flags = []
        if intent in {EntityIntent.CEO_LOOKUP, EntityIntent.FOUNDER_LOOKUP, EntityIntent.COMPANY_DETAILS, EntityIntent.LEGITIMACY_CHECK} and not entity_name:
            flags.append("missing_entity")
        return EntityQuery(
            original_query=raw,
            intent=intent,
            entity_name=entity_name,
            entity_type=resolved["entity_type"],
            requested_attribute=_attribute_for_intent(intent),
            platform=_platform(lower),
            ambiguity_flags=tuple(flags),
        )

    def _intent(self, lower: str) -> str:
        if re.search(r"\b(ceo|chief executive)\b", lower):
            return EntityIntent.CEO_LOOKUP
        if re.search(r"\b(founder|founded|cofounder|co-founder|started)\b", lower):
            return EntityIntent.FOUNDER_LOOKUP
        if re.search(r"\b(instagram|insta|social handle|social profile|facebook|youtube|x profile|twitter)\b", lower):
            return EntityIntent.OFFICIAL_SOCIAL_PROFILE
        if "linkedin" in lower:
            return EntityIntent.LINKEDIN_PROFILE
        if re.search(r"\b(real|legit|legitimate|registered|verify|exists?)\b", lower):
            return EntityIntent.LEGITIMACY_CHECK
        if re.search(r"\b(company|startup|what does|what is|details|about)\b", lower):
            return EntityIntent.COMPANY_DETAILS
        return EntityIntent.UNKNOWN


def _attribute_for_intent(intent: str) -> str:
    return {
        EntityIntent.CEO_LOOKUP: "ceo",
        EntityIntent.FOUNDER_LOOKUP: "founder",
        EntityIntent.OFFICIAL_SOCIAL_PROFILE: "official_social_profile",
        EntityIntent.LINKEDIN_PROFILE: "linkedin_profile",
        EntityIntent.COMPANY_DETAILS: "company_details",
        EntityIntent.LEGITIMACY_CHECK: "legitimacy",
    }.get(intent, "")


def _platform(lower: str) -> str:
    if "instagram" in lower or "insta" in lower:
        return "instagram"
    if "linkedin" in lower:
        return "linkedin"
    if "facebook" in lower:
        return "facebook"
    if "youtube" in lower:
        return "youtube"
    if "twitter" in lower or "x profile" in lower:
        return "x"
    return ""


def detect_entity_intent(query: str) -> EntityQuery:
    return EntityIntentDetector().detect(query)
