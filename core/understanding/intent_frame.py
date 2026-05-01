from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class CorrectionCandidate:
    token: str
    candidate: str
    kind: str
    score: float


@dataclass(frozen=True)
class EntityMention:
    text: str
    canonical: str
    kind: str
    score: float = 1.0
    company: str = ""
    official_domain: str = ""


@dataclass(frozen=True)
class RelationFrame:
    relation: str
    canonical: str
    variants: tuple[str, ...] = ()
    score: float = 0.0


@dataclass(frozen=True)
class SearchPlan:
    official: tuple[str, ...] = ()
    news: tuple[str, ...] = ()
    contradiction: tuple[str, ...] = ()
    background: tuple[str, ...] = ()
    technical: tuple[str, ...] = ()
    regional: tuple[str, ...] = ()
    fallback: tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, List[str]]:
        return {
            "official": list(self.official),
            "news": list(self.news),
            "contradiction": list(self.contradiction),
            "background": list(self.background),
            "technical": list(self.technical),
            "regional": list(self.regional),
            "fallback": list(self.fallback),
        }


@dataclass(frozen=True)
class MeaningFrame:
    original_query: str = ""
    normalized_query: str = ""
    user_intent: str = ""
    route_hint: str = ""
    claim_type: str = ""
    primary_subject: str = ""
    protected_entities: tuple[str, ...] = ()
    relation: str = ""
    target_attribute: str = ""
    disallowed_subject_drifts: tuple[str, ...] = ()
    ambiguity_flags: tuple[str, ...] = ()
    confidence: float = 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "original_query": self.original_query,
            "normalized_query": self.normalized_query,
            "user_intent": self.user_intent,
            "route_hint": self.route_hint,
            "claim_type": self.claim_type,
            "primary_subject": self.primary_subject,
            "protected_entities": list(self.protected_entities),
            "relation": self.relation,
            "target_attribute": self.target_attribute,
            "disallowed_subject_drifts": list(self.disallowed_subject_drifts),
            "ambiguity_flags": list(self.ambiguity_flags),
            "confidence": round(float(self.confidence or 0.0), 3),
        }


@dataclass(frozen=True)
class IntentFrame:
    original_query: str
    cleaned_query: str
    intent: str
    normalized_query: str = ""
    language_hint: str = ""
    intent_hint: str = ""
    route_hint: str = ""
    entities: Dict[str, str] = field(default_factory=dict)
    entity_mentions: tuple[EntityMention, ...] = ()
    relation: str = ""
    relation_frame: RelationFrame | None = None
    normalized_question: str = ""
    task_hints: Dict[str, object] = field(default_factory=dict)
    document_hints: Dict[str, object] = field(default_factory=dict)
    code_hints: Dict[str, object] = field(default_factory=dict)
    entity_intelligence_summary: Dict[str, object] = field(default_factory=dict)
    search_plan: SearchPlan = field(default_factory=SearchPlan)
    correction_candidates: tuple[CorrectionCandidate, ...] = ()
    confidence: float = 0.0
    needs_llm_rewrite: bool = False
    raw_query_priority: str = "normal"
    ambiguity_flags: tuple[str, ...] = ()
    query_plan_summary: Dict[str, object] = field(default_factory=dict)
    meaning_frame: MeaningFrame | None = None

    @property
    def subject(self) -> str:
        return self.entities.get("country") or self.entities.get("person") or ""

    @property
    def object(self) -> str:
        return (
            self.entities.get("product")
            or self.entities.get("company")
            or self.entities.get("tool_framework")
            or self.entities.get("person")
            or ""
        )
