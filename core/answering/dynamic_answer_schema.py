from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class AnswerSchema:
    key: str
    headings: Tuple[str, ...]


SCHEMAS: Dict[str, AnswerSchema] = {
    "rumour_verification": AnswerSchema(
        key="rumour_verification",
        headings=(
            "Rumour status",
            "Best-supported status",
            "Related evidence",
            "What this does NOT prove",
            "What may be causing confusion",
            "Confidence",
            "Sources checked",
        ),
    ),
    "no_usable_evidence": AnswerSchema(
        key="no_usable_evidence",
        headings=(
            "What I could not verify",
            "What I checked",
            "Best next checks",
            "Confidence",
        ),
    ),
    "related_evidence_only": AnswerSchema(
        key="related_evidence_only",
        headings=(
            "Claim status",
            "Closest related evidence",
            "What is confirmed",
            "What is not confirmed",
            "Likely confusion",
            "Confidence",
            "Sources",
        ),
    ),
    "package_version": AnswerSchema(
        key="package_version",
        headings=("Latest version", "Source-of-record", "Confidence"),
    ),
    "entity_lookup": AnswerSchema(
        key="entity_lookup",
        headings=(
            "Best-supported candidate",
            "Why this candidate",
            "What is uncertain",
            "Other possible matches",
            "Sources checked",
            "Confidence",
        ),
    ),
    "social_profile": AnswerSchema(
        key="social_profile",
        headings=(
            "Likely official profile",
            "Why this profile",
            "Verification signals",
            "Possible alternatives",
            "Confidence",
            "Safety note",
        ),
    ),
    "legitimacy_check": AnswerSchema(
        key="legitimacy_check",
        headings=(
            "Legitimacy status",
            "Public evidence found",
            "What is missing",
            "Caution",
            "Sources checked",
        ),
    ),
    "comparison": AnswerSchema(
        key="comparison",
        headings=("Quick verdict", "Comparison", "Best choice by use case", "Trade-offs", "Sources"),
    ),
    "troubleshooting": AnswerSchema(
        key="troubleshooting",
        headings=("Likely cause", "Fix", "Why it works", "If it still fails"),
    ),
    "explanation": AnswerSchema(
        key="explanation",
        headings=("Simple explanation", "Example", "Common mistake", "Quick recap"),
    ),
    "default_research": AnswerSchema(
        key="default_research",
        headings=("Best-supported answer", "Why this answer", "Confidence", "What to treat carefully", "Sources"),
    ),
}


def get_schema(key: str) -> AnswerSchema:
    return SCHEMAS.get(str(key or "").strip(), SCHEMAS["default_research"])
