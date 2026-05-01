from __future__ import annotations

import re
from typing import Iterable, List

from .entity_models import EntityEvidence, ROLE_ALIASES, SOURCE_AUTHORITY, SOURCE_TIER


class EntityEvidenceRanker:
    def rank(self, rows: Iterable[EntityEvidence]) -> List[EntityEvidence]:
        public_rows = [
            row for row in rows
            if row.is_public and not row.requires_login and row.source_type not in {"private_profile", "login_only"}
        ]
        return sorted(public_rows, key=self.score, reverse=True)

    def score(self, row: EntityEvidence) -> float:
        base = SOURCE_AUTHORITY.get(row.source_type, 0.3)
        if row.supports_claim:
            base += 0.08
        if row.candidate_name:
            base += 0.04
        if row.company_match:
            base += 0.14
        if row.target_entity_match:
            base += 0.1
        if row.role_applies_to_person:
            base += 0.08
        text = " ".join(
            str(value or "")
            for value in (
                row.title,
                row.snippet,
                row.url,
                row.attribute,
                row.candidate_name,
                row.role_holder_detected,
                row.extracted_role,
            )
        ).lower()
        if row.source_type == "official_website" and re.search(r"\b(about|team|leadership|management|founder|ceo)\b", text):
            base += 0.06
        if row.source_type in {"company_linkedin", "person_linkedin"} and re.search(r"\b(ceo|founder|owner|chief executive)\b", text):
            base += 0.05
        if row.source_type == "company_linkedin" and row.role_applies_to_person and row.company_match:
            base += 0.09
        if row.source_type in {"registry_directory", "government_registry"} and re.search(r"\b(director|registered|incorporated|company)\b", text):
            base += 0.04
        if row.role_match:
            base += 0.08
        if row.contradicts_claim:
            base += 0.02
        if row.supported_role == "team_member":
            base -= 0.02
        if row.rejection_reason in {"unrelated_company", "entity_mismatch"}:
            base -= 0.55
        if row.requested_role and row.supported_role and not row.role_match:
            base -= 0.18
        if row.source_tier == "tier3" and row.requested_role in {"ceo", "founder"} and row.role_match:
            base -= 0.22
        if row.source_tier == "tier3" and row.requested_role in {"ceo", "founder"} and not row.company_match:
            base -= 0.28
        if row.rejection_reason:
            base -= 0.18
        if re.search(r"\b(private|login required|sign in|directory profile)\b", text):
            base -= 0.12
        if not row.company_match and not row.target_entity_match:
            base -= 0.3
        if row.source_relevance_score:
            base += min(0.18, max(-0.18, row.source_relevance_score - 0.5))
        return round(min(1.0, base), 3)


def detect_supported_role(*, requested_role: str, text: str) -> str:
    lowered = str(text or "").lower()
    requested = str(requested_role or "").strip().lower()
    founder_ceo = re.search(r"\b(founder\s*(?:&|and)\s*ceo|ceo\s*(?:&|and)\s*founder)\b", lowered)
    if founder_ceo:
        return "founder_ceo"
    if requested == "ceo" and any(token in lowered for token in ROLE_ALIASES["ceo"]):
        return "ceo"
    if requested == "founder" and any(token in lowered for token in ROLE_ALIASES["founder"]):
        return "founder"
    if any(token in lowered for token in {"core team", "team member", "member of the team"}):
        return "team_member"
    if any(token in lowered for token in ROLE_ALIASES["employee"]):
        return "employee"
    if any(token in lowered for token in ROLE_ALIASES["director"]):
        return "director"
    return ""


def role_match_metadata(*, requested_role: str, supported_role: str) -> tuple[bool, str]:
    requested = str(requested_role or "").strip().lower()
    supported = str(supported_role or "").strip().lower()
    if not requested or not supported:
        return False, "no_exact_role_evidence"
    if supported == "founder_ceo" and requested in {"ceo", "founder"}:
        return True, ""
    if requested == supported:
        return True, ""
    if requested in {"ceo", "founder"} and supported in {"employee", "team_member", "member", "worker"}:
        return False, "employee_evidence_does_not_verify_requested_role"
    return False, f"supported_role_is_{supported}_not_{requested}"


def source_tier_for(source_type: str) -> str:
    return SOURCE_TIER.get(str(source_type or "").strip(), "tier3")


def rank_entity_evidence(rows: Iterable[EntityEvidence]) -> List[EntityEvidence]:
    return EntityEvidenceRanker().rank(rows)
