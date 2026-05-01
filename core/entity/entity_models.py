from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


class EntityIntent:
    CEO_LOOKUP = "ceo_lookup"
    FOUNDER_LOOKUP = "founder_lookup"
    COMPANY_DETAILS = "company_details"
    OFFICIAL_SOCIAL_PROFILE = "official_social_profile"
    LINKEDIN_PROFILE = "linkedin_profile"
    LEGITIMACY_CHECK = "legitimacy_check"
    UNKNOWN = "unknown"


PUBLIC_ONLY_POLICY = (
    "Use public web evidence only.",
    "Do not bypass login or private profile access.",
    "Do not expose private or sensitive personal data.",
    "Do not confirm weak candidate evidence as verified fact.",
)


SOURCE_AUTHORITY = {
    "official_website": 1.0,
    "government_registry": 0.96,
    "verified_social_link": 0.92,
    "company_linkedin": 0.88,
    "person_linkedin": 0.84,
    "reputable_news": 0.76,
    "registry_directory": 0.66,
    "search_snippet": 0.42,
    "random_social": 0.22,
}

SOURCE_TIER = {
    "official_website": "tier1",
    "government_registry": "tier1",
    "verified_social_link": "tier1",
    "company_linkedin": "tier1",
    "person_linkedin": "tier2",
    "reputable_news": "tier2",
    "registry_directory": "tier2",
    "search_snippet": "tier3",
    "random_social": "tier3",
}

ROLE_ALIASES = {
    "ceo": {"ceo", "chief executive officer", "founder & ceo", "founder and ceo", "current ceo"},
    "founder": {"founder", "co-founder", "cofounder", "owner", "proprietor"},
    "employee": {"employee", "worker", "member", "staff", "team member", "associate"},
    "director": {"director", "managing director"},
}


@dataclass(frozen=True)
class EntityQuery:
    original_query: str
    intent: str
    entity_name: str = ""
    entity_type: str = "unknown"
    requested_attribute: str = ""
    platform: str = ""
    ambiguity_flags: tuple[str, ...] = ()
    public_only_policy: tuple[str, ...] = PUBLIC_ONLY_POLICY

    @property
    def has_entity(self) -> bool:
        return bool(self.entity_name.strip())


@dataclass(frozen=True)
class EntitySourcePlan:
    entity_name: str
    intent: str
    lanes: Dict[str, List[str]] = field(default_factory=dict)
    required_lanes: tuple[str, ...] = ()
    public_only_policy: tuple[str, ...] = PUBLIC_ONLY_POLICY

    def flatten(self) -> List[str]:
        out: List[str] = []
        seen = set()
        for lane in ("official_website", "linkedin", "registry_directory", "news_articles", "social_profiles", "general_web"):
            for query in self.lanes.get(lane) or []:
                key = query.lower().strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(query)
        return out


@dataclass(frozen=True)
class EntityEvidence:
    title: str
    url: str = ""
    source_type: str = "search_snippet"
    snippet: str = ""
    candidate_name: str = ""
    attribute: str = ""
    requested_role: str = ""
    supported_role: str = ""
    role_match: bool = False
    role_mismatch_reason: str = ""
    contradicts_claim: bool = False
    source_tier: str = "tier3"
    strongest_source_type: str = ""
    extraction_quality: float = 0.0
    freshness: str = ""
    usable_for_verification: bool = False
    rejection_reason: str = ""
    supports_claim: bool = False
    company_match: bool = False
    target_entity_match: bool = False
    role_holder_detected: str = ""
    extracted_role: str = ""
    role_applies_to_person: bool = False
    source_relevance_score: float = 0.0
    is_public: bool = True
    requires_login: bool = False
    confidence: float = 0.0


@dataclass(frozen=True)
class EntityProfileCandidate:
    handle: str
    platform: str
    url: str = ""
    evidence_type: str = "random_social"
    name_match: float = 0.0
    domain_match: bool = False
    linked_from_official_site: bool = False
    requires_login: bool = False
    is_private: bool = False
    confidence: float = 0.0
    status: str = "profile_unverified"


@dataclass(frozen=True)
class EntityAnswer:
    mode: str
    answer: str
    confidence: str
    why: str
    uncertainty: str
    sources_checked: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    verification_state: str = "unknown"
    selected_candidate: str = ""
    candidate_count: int = 0
    official_source_found: bool = False
    linkedin_source_found: bool = False
    registry_source_found: bool = False
    requested_role: str = ""
    supported_role: str = ""
    role_match: bool = False
    role_mismatch_reason: str = ""
    conflict_detected: bool = False
    exact_role_verified: bool = False
    strongest_source_type: str = ""
    evidence_strength: str = "weak"
    source_agreement: str = "unknown"
    disambiguation_needed: bool = False
    confidence_reason: str = ""
    search_depth_used: str = "standard"
    search_lanes_used: tuple[str, ...] = ()
    source_tiers_found: tuple[str, ...] = ()
    public_only_policy: tuple[str, ...] = PUBLIC_ONLY_POLICY

    def as_text(self) -> str:
        lines = [
            f"Answer: {self.answer}",
            "",
            f"Confidence: {self.confidence}.",
            f"Why: {self.why}",
            f"What is uncertain: {self.uncertainty}",
            "",
            "Entity trust metadata:",
            f"- entity_answer_mode: {self.mode}",
            f"- verification_state: {self.verification_state}",
            f"- selected_candidate: {self.selected_candidate or 'n/a'}",
            f"- candidate_count: {self.candidate_count}",
            f"- official_source_found: {'yes' if self.official_source_found else 'no'}",
            f"- linkedin_source_found: {'yes' if self.linkedin_source_found else 'no'}",
            f"- registry_source_found: {'yes' if self.registry_source_found else 'no'}",
            f"- requested_role: {self.requested_role or 'n/a'}",
            f"- supported_role: {self.supported_role or 'n/a'}",
            f"- role_match: {'yes' if self.role_match else 'no'}",
            f"- role_mismatch_reason: {self.role_mismatch_reason or 'n/a'}",
            f"- conflict_detected: {'yes' if self.conflict_detected else 'no'}",
            f"- exact_role_verified: {'yes' if self.exact_role_verified else 'no'}",
            f"- evidence_strength: {self.evidence_strength}",
            f"- strongest_source_type: {self.strongest_source_type or 'n/a'}",
            f"- source_agreement: {self.source_agreement}",
            f"- disambiguation_needed: {'yes' if self.disambiguation_needed else 'no'}",
            f"- search_depth_used: {self.search_depth_used}",
            f"- search_lanes_used: {', '.join(self.search_lanes_used) if self.search_lanes_used else 'n/a'}",
            f"- source_tiers_found: {', '.join(self.source_tiers_found) if self.source_tiers_found else 'n/a'}",
            f"- confidence_reason: {self.confidence_reason or 'n/a'}",
            "",
            "Sources checked:",
        ]
        lines.extend(f"- {item}" for item in (self.sources_checked or ("No public sources supplied.",)))
        if self.alternatives:
            lines.extend(["", "Candidate alternatives:"])
            lines.extend(f"- {item}" for item in self.alternatives)
        lines.extend(["", "Public-only policy:"])
        lines.extend(f"- {item}" for item in self.public_only_policy)
        return "\n".join(lines).strip()
