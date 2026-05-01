from __future__ import annotations

from typing import Dict, List

from .entity_models import EntityIntent, EntityQuery, EntitySourcePlan


class EntitySourcePlanner:
    def plan(self, entity_query: EntityQuery) -> EntitySourcePlan:
        entity = entity_query.entity_name or "<entity>"
        role = str(entity_query.requested_attribute or "").strip().upper() or "CEO"
        exact_role_query = "founder" if entity_query.intent == EntityIntent.FOUNDER_LOOKUP else "ceo" if entity_query.intent == EntityIntent.CEO_LOOKUP else role.lower()
        founder_ceo = "Founder & CEO" if entity_query.intent in {EntityIntent.CEO_LOOKUP, EntityIntent.FOUNDER_LOOKUP} else role
        lanes: Dict[str, List[str]] = {
            "official_website": [
                f"{entity} official website",
                f"{entity} about team leadership",
                f"{entity} about us leadership {role}",
                f"{entity} team founder {role}",
                f"{entity} {exact_role_query} official website",
                f"{entity} core team {role}",
            ],
            "linkedin": [
                f"{entity} LinkedIn company page",
                f"{entity} founder CEO LinkedIn",
                f"site:linkedin.com/company \"{entity}\" \"{role}\"",
                f"site:linkedin.com/in \"{entity}\" \"{role}\"",
                f"site:linkedin.com/company \"{entity}\" \"{exact_role_query}\"",
                f"{entity} LinkedIn {founder_ceo}",
                f"site:linkedin.com/company/{_dash_slug(entity)} {founder_ceo}",
                f"site:linkedin.com/posts/{_dash_slug(entity)} {founder_ceo}",
                f"{entity} core team {founder_ceo}",
            ],
            "registry_directory": [
                f"{entity} company registry",
                f"{entity} MCA registry ROC",
                f"{entity} Crunchbase Tracxn RocketReach Apollo",
                f"{entity} business registration directors",
                f"{entity} founder proprietor owner registry",
            ],
            "news_articles": [
                f"{entity} founder CEO interview",
                f"{entity} news article leadership",
                f"{entity} {role} press release",
                f"{entity} {exact_role_query} interview",
                f"{entity} {founder_ceo}",
            ],
            "social_profiles": [
                f"{entity} official Instagram",
                f"{entity} official social profiles",
            ],
            "general_web": [
                f"{entity} company details",
                f"{entity} public profile",
                f"{entity} founder {role}",
                f"{entity} leadership team",
                f"{entity} {exact_role_query}",
                f"{entity} employee team member",
                f"{entity} {founder_ceo}",
            ],
        }
        required = ["official_website", "linkedin", "general_web"]
        if entity_query.intent in {EntityIntent.CEO_LOOKUP, EntityIntent.FOUNDER_LOOKUP}:
            required.extend(["news_articles", "registry_directory"])
        if entity_query.intent == EntityIntent.OFFICIAL_SOCIAL_PROFILE:
            required.append("social_profiles")
        if entity_query.intent == EntityIntent.LINKEDIN_PROFILE:
            required.append("linkedin")
        if entity_query.intent == EntityIntent.LEGITIMACY_CHECK:
            required.extend(["registry_directory", "social_profiles"])
        return EntitySourcePlan(
            entity_name=entity_query.entity_name,
            intent=entity_query.intent,
            lanes={key: _dedupe(value) for key, value in lanes.items()},
            required_lanes=tuple(_dedupe(required)),
        )


def _dedupe(values):
    out = []
    seen = set()
    for value in values:
        key = str(value).lower()
        if key not in seen:
            seen.add(key)
            out.append(str(value))
    return out


def _dash_slug(value: str) -> str:
    parts = [chunk.strip().lower() for chunk in str(value or "").replace("/", " ").split() if chunk.strip()]
    return "-".join(parts)
