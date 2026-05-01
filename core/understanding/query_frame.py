from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from typing import List

from taos.core.semantic.query_normalizer import normalize_user_query
from taos.core.understanding.query_canonicalizer import (
    BaseQueryCanonicalizer,
    CanonicalizationResult,
    NullQueryCanonicalizer,
    SemanticQueryCanonicalizer,
    validate_canonicalization_result,
)
from taos.core.understanding.query_fastpath_rules import RuleBasedFastPathCanonicalizer
from taos.core.understanding.query_language import detect_language_profile


@dataclass(frozen=True)
class QueryFrame:
    original_query: str
    normalized_query: str
    detected_language: str
    detected_script: str
    canonical_query: str
    intent: str
    lookup_type: str
    entity: str
    role: str
    answer_language: str
    search_queries: List[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "query_frame_builder"
    warnings: List[str] = field(default_factory=list)
    ambiguity_flags: List[str] = field(default_factory=list)
    language_confidence: float = 0.0
    mixed_language_flag: bool = False
    transliteration_detected: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class QueryFrameBuilder:
    MIN_FASTPATH_CONFIDENCE = 0.80
    MIN_SEMANTIC_CONFIDENCE = 0.75

    def __init__(
        self,
        *,
        semantic_canonicalizer: BaseQueryCanonicalizer | None = None,
        fastpath: RuleBasedFastPathCanonicalizer | None = None,
    ) -> None:
        self._fastpath = fastpath or RuleBasedFastPathCanonicalizer()
        self._semantic = semantic_canonicalizer or SemanticQueryCanonicalizer()

    def build(self, query: str) -> QueryFrame:
        original_query = str(query or "").strip()
        normalized_query = normalize_user_query(original_query)
        language = detect_language_profile(original_query)

        fast = self._fastpath.canonicalize(
            normalized_query=normalized_query,
            language_profile=language,
        )
        if fast and float(fast.confidence) >= self.MIN_FASTPATH_CONFIDENCE:
            return self._build_frame(
                original_query=original_query,
                normalized_query=normalized_query,
                detected_language=language.detected_language,
                detected_script=language.detected_script,
                canonical_query=fast.canonical_query,
                intent=fast.intent,
                lookup_type=fast.lookup_type,
                entity=fast.entity,
                role=fast.role,
                answer_language=language.answer_language,
                confidence=float(fast.confidence),
                source=fast.source,
                warnings=[],
                ambiguity_flags=[],
                language_confidence=language.language_confidence,
                mixed_language_flag=language.mixed_language_flag,
                transliteration_detected=language.transliteration_detected,
            )

        if self._semantic_enabled():
            semantic_raw = self._semantic.canonicalize(original_query)
            semantic = validate_canonicalization_result(
                CanonicalizationResult(
                    **{
                        **semantic_raw.__dict__,
                        "original_query": original_query,
                        "normalized_query": normalized_query,
                        "detected_language": semantic_raw.detected_language or language.detected_language,
                        "detected_script": semantic_raw.detected_script or language.detected_script,
                        "answer_language": semantic_raw.answer_language or language.answer_language,
                    }
                )
            )
            if semantic.intent != "unknown" and float(semantic.confidence) >= self.MIN_SEMANTIC_CONFIDENCE:
                return self._build_frame(
                    original_query=original_query,
                    normalized_query=normalized_query,
                    detected_language=semantic.detected_language,
                    detected_script=semantic.detected_script,
                    canonical_query=semantic.canonical_query,
                    intent=semantic.intent,
                    lookup_type=semantic.lookup_type,
                    entity=semantic.entity,
                    role=semantic.role,
                    answer_language=semantic.answer_language,
                    confidence=semantic.confidence,
                    source=semantic.source,
                    warnings=list(semantic.warnings or []),
                    ambiguity_flags=list(semantic.ambiguity_flags or []),
                    language_confidence=language.language_confidence,
                    mixed_language_flag=language.mixed_language_flag,
                    transliteration_detected=language.transliteration_detected,
                )
            return self._unknown_frame(
                original_query=original_query,
                normalized_query=normalized_query,
                language=language,
                warnings=list(semantic.warnings or ["semantic_low_confidence_or_unknown"]),
            )

        return self._unknown_frame(
            original_query=original_query,
            normalized_query=normalized_query,
            language=language,
            warnings=["semantic_canonicalizer_disabled"],
        )

    @staticmethod
    def _semantic_enabled() -> bool:
        observe_only = str(os.getenv("QUERY_FRAME_OBSERVE_ONLY", "true")).strip().lower() == "true"
        _ = observe_only  # explicit read for phase policy visibility, no behavioral effect here.
        return str(os.getenv("QUERY_FRAME_SEMANTIC_CANONICALIZER_ENABLED", "false")).strip().lower() == "true"

    def _unknown_frame(self, *, original_query: str, normalized_query: str, language, warnings: list[str]) -> QueryFrame:
        return self._build_frame(
            original_query=original_query,
            normalized_query=normalized_query,
            detected_language=language.detected_language,
            detected_script=language.detected_script,
            canonical_query=normalized_query,
            intent="unknown",
            lookup_type="unknown",
            entity="",
            role="",
            answer_language=language.answer_language,
            confidence=0.0,
            source="unknown_fallback",
            warnings=warnings,
            ambiguity_flags=[],
            language_confidence=language.language_confidence,
            mixed_language_flag=language.mixed_language_flag,
            transliteration_detected=language.transliteration_detected,
        )

    def _build_frame(
        self,
        *,
        original_query: str,
        normalized_query: str,
        detected_language: str,
        detected_script: str,
        canonical_query: str,
        intent: str,
        lookup_type: str,
        entity: str,
        role: str,
        answer_language: str,
        confidence: float,
        source: str,
        warnings: list[str],
        ambiguity_flags: list[str],
        language_confidence: float,
        mixed_language_flag: bool,
        transliteration_detected: bool,
    ) -> QueryFrame:
        search_queries = [str(canonical_query or "").strip()]
        if original_query and original_query != canonical_query:
            search_queries.append(original_query)
        return QueryFrame(
            original_query=original_query,
            normalized_query=normalized_query,
            detected_language=str(detected_language or "unknown"),
            detected_script=str(detected_script or "Unknown"),
            canonical_query=str(canonical_query or "").strip(),
            intent=str(intent or "unknown"),
            lookup_type=str(lookup_type or "unknown"),
            entity=str(entity or "").strip(),
            role=str(role or "").strip().lower(),
            answer_language=str(answer_language or detected_language or "unknown"),
            search_queries=search_queries,
            confidence=round(float(confidence or 0.0), 3),
            source=str(source or "query_frame_builder"),
            warnings=list(warnings or []),
            ambiguity_flags=list(ambiguity_flags or []),
            language_confidence=round(float(language_confidence or 0.0), 3),
            mixed_language_flag=bool(mixed_language_flag),
            transliteration_detected=bool(transliteration_detected),
        )


def compare_query_frame_to_selected_route(query_frame: QueryFrame, selected_route: str) -> dict[str, str]:
    selected = str(selected_route or "").strip().lower()
    suggested = str(query_frame.intent or "unknown").strip().lower()
    confidence = float(query_frame.confidence or 0.0)
    if suggested in {"", "unknown"}:
        return {
            "current_selected_route": selected,
            "query_frame_suggested_family": "unknown",
            "route_alignment": "unknown",
            "mismatch_reason": "query_frame_unsupported",
        }
    if confidence < 0.5:
        return {
            "current_selected_route": selected,
            "query_frame_suggested_family": suggested,
            "route_alignment": "unknown",
            "mismatch_reason": "query_frame_low_confidence",
        }
    if suggested == "entity_lookup":
        if selected == "entity_lookup":
            return {
                "current_selected_route": selected,
                "query_frame_suggested_family": suggested,
                "route_alignment": "aligned",
                "mismatch_reason": "",
            }
        reason_map = {
            "standard_task": "current_route_standard_but_query_frame_entity_lookup",
            "deep_research": "current_route_research_but_query_frame_entity_lookup",
            "official_search": "current_route_research_but_query_frame_entity_lookup",
            "news_search": "current_route_research_but_query_frame_entity_lookup",
            "comparison_search": "current_route_research_but_query_frame_entity_lookup",
        }
        return {
            "current_selected_route": selected,
            "query_frame_suggested_family": suggested,
            "route_alignment": "mismatch",
            "mismatch_reason": reason_map.get(selected, "route_unknown"),
        }
    return {
        "current_selected_route": selected,
        "query_frame_suggested_family": suggested,
        "route_alignment": "unknown",
        "mismatch_reason": "route_unknown",
    }


# Backward-compatible exports for existing tests/import sites.
__all__ = [
    "QueryFrame",
    "QueryFrameBuilder",
    "compare_query_frame_to_selected_route",
    "CanonicalizationResult",
    "NullQueryCanonicalizer",
]
