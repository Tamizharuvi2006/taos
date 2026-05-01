from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Protocol


SUPPORTED_INTENTS = {"entity_lookup", "unknown"}
SUPPORTED_LOOKUP_TYPES = {
    "founder_lookup",
    "ceo_lookup",
    "linkedin_profile",
    "official_website",
    "business_legitimacy",
    "unknown",
}
SUPPORTED_ROLES = {"", "ceo", "founder"}


@dataclass(frozen=True)
class CanonicalizationResult:
    original_query: str = ""
    normalized_query: str = ""
    detected_language: str = "unknown"
    detected_script: str = "Unknown"
    canonical_query: str = ""
    intent: str = "unknown"
    lookup_type: str = "unknown"
    entity: str = ""
    role: str = ""
    answer_language: str = "unknown"
    confidence: float = 0.0
    source: str = "semantic_canonicalizer_unknown"
    warnings: list[str] = field(default_factory=list)
    ambiguity_flags: list[str] = field(default_factory=list)


class BaseQueryCanonicalizer(Protocol):
    def canonicalize(self, query: str) -> CanonicalizationResult:
        ...


def validate_canonicalization_result(result: CanonicalizationResult) -> CanonicalizationResult:
    intent = str(result.intent or "unknown").strip().lower()
    lookup = str(result.lookup_type or "unknown").strip().lower()
    role = str(result.role or "").strip().lower()
    confidence = float(result.confidence or 0.0)
    warnings = list(result.warnings or [])

    if intent not in SUPPORTED_INTENTS:
        warnings.append("invalid_intent")
        intent = "unknown"
    if lookup not in SUPPORTED_LOOKUP_TYPES:
        warnings.append("invalid_lookup_type")
        lookup = "unknown"
    if role not in SUPPORTED_ROLES:
        warnings.append("invalid_role")
        role = ""
    if confidence < 0.0 or confidence > 1.0:
        warnings.append("invalid_confidence")
        confidence = 0.0
    if intent == "entity_lookup":
        if not str(result.entity or "").strip():
            warnings.append("missing_entity")
            intent = "unknown"
            lookup = "unknown"
            confidence = 0.0
        if not str(result.canonical_query or "").strip():
            warnings.append("missing_canonical_query")
            intent = "unknown"
            lookup = "unknown"
            confidence = 0.0
    if "answer:" in str(result.canonical_query or "").lower():
        warnings.append("suspicious_answer_like_canonical_query")
        intent = "unknown"
        lookup = "unknown"
        confidence = 0.0

    return CanonicalizationResult(
        original_query=str(result.original_query or ""),
        normalized_query=str(result.normalized_query or ""),
        detected_language=str(result.detected_language or "unknown"),
        detected_script=str(result.detected_script or "Unknown"),
        canonical_query=str(result.canonical_query or ""),
        intent=intent,
        lookup_type=lookup,
        entity=str(result.entity or ""),
        role=role,
        answer_language=str(result.answer_language or result.detected_language or "unknown"),
        confidence=confidence,
        source=str(result.source or "semantic_canonicalizer_unknown"),
        warnings=warnings,
        ambiguity_flags=list(result.ambiguity_flags or []),
    )


class NullQueryCanonicalizer:
    def canonicalize(self, query: str) -> CanonicalizationResult:
        return CanonicalizationResult(
            original_query=str(query or ""),
            normalized_query=str(query or ""),
            intent="unknown",
            lookup_type="unknown",
            confidence=0.0,
            source="null_query_canonicalizer",
            warnings=["semantic_canonicalizer_disabled_or_unavailable"],
        )


class SemanticQueryCanonicalizer:
    """Validated adapter for model-based canonicalization (feature-flag gated)."""

    def __init__(self, adapter: BaseQueryCanonicalizer | None = None) -> None:
        self._adapter = adapter

    @staticmethod
    def enabled() -> bool:
        return str(os.getenv("QUERY_FRAME_SEMANTIC_CANONICALIZER_ENABLED", "false")).strip().lower() == "true"

    def canonicalize(self, query: str) -> CanonicalizationResult:
        if not self.enabled():
            return NullQueryCanonicalizer().canonicalize(query)
        if self._adapter is None:
            result = NullQueryCanonicalizer().canonicalize(query)
            return CanonicalizationResult(
                **{**result.__dict__, "source": "semantic_canonicalizer_unconfigured"}
            )
        raw = self._adapter.canonicalize(query)
        return validate_canonicalization_result(raw)
