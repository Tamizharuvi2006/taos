from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .route_rules import RuleDecision


PACKAGE_VERSION_PATTERNS = (
    re.compile(r"\b(?P<entity>[a-z][a-z0-9_.-]{1,40})\s+(?:latest|current|newest)?\s*(?:version|release)\b", re.I),
    re.compile(r"\b(?:latest|current|newest)\s+(?:version|release)\s+(?:of\s+)?(?P<entity>[a-z][a-z0-9_.-]{1,40})\b", re.I),
    re.compile(r"\b(?P<entity>[a-z][a-z0-9_.-]{1,40})\s+(?:ka|oda|ki|की|का)?\s*(?:latest|current)?\s*(?:version|версión|versione)\b", re.I),
)

MULTILINGUAL_VERSION_HINTS = (
    "derniere version",
    "dernière version",
    "última versión",
    "ultima version",
    "neueste version",
    "最新版本",
    "最新版",
    "latest version",
    "current version",
)

STOP_ENTITIES = {
    "what",
    "which",
    "who",
    "how",
    "est",
    "la",
    "le",
    "les",
    "de",
    "des",
    "du",
    "quelle",
    "quel",
    "derniere",
    "dernière",
    "ultima",
    "última",
    "neueste",
    "ka",
    "ki",
    "kya",
    "hai",
    "oda",
    "enna",
    "latest",
    "current",
    "version",
    "release",
    "official",
    "pricing",
    "price",
    "docs",
    "documentation",
}

OFFICIAL_MARKERS = re.compile(
    r"\b(official|docs?|documentation|pricing|price|changelog|release\s+notes?|api|sdk|policy|standard|specs?|specification|government|regulator|visa|tax|medical|health|finance|legal)\b",
    re.I,
)
FRESHNESS_MARKERS = re.compile(
    r"\b(latest|current|today|now|recent|new|newest|released?|changed|updates?|202[0-9]|this\s+week|this\s+month)\b",
    re.I,
)
COMPARISON_MARKERS = re.compile(r"\b(compare|comparison|vs\.?|versus|better|best|difference|tradeoffs?)\b", re.I)
RESEARCH_MARKERS = re.compile(
    r"\b(research|deep|analy[sz]e|analysis|investigate|comprehensive|thorough|market|trend|forecast|frameworks?|production|architecture)\b",
    re.I,
)
NEWS_MARKERS = re.compile(r"\b(news|headline|breaking|latest\s+news|today|current\s+status)\b", re.I)
DOC_MARKERS = re.compile(r"\b(pdf|document|uploaded|file|from\s+this|notes|chapter|unit)\b", re.I)
TASK_MARKERS = re.compile(r"\b(run|execute|fix|debug|build|create|send|generate|write|implement|deploy|install|test|refactor|schedule)\b", re.I)
SMALLTALK_MARKERS = re.compile(r"^\s*(hi+|hey+|hello+|yo+|sup|thanks|thank\s+you|bye|ok(?:ay)?)\s*[!.?]*\s*$", re.I)
HIGH_STAKES_MARKERS = re.compile(
    r"\b(medical|health|medicine|dosage|legal|law|court|financial|investment|tax|immigration|visa|regulation|compliance|safety)\b",
    re.I,
)


@dataclass
class HybridRouteSignal:
    normalized_query: str
    normalized_intent: str
    route: str
    confidence: float
    reason: str
    entities: List[str] = field(default_factory=list)
    freshness_required: bool = False
    official_preferred: bool = False
    high_stakes: bool = False
    preferred_tools: List[str] = field(default_factory=list)
    source_policy: str = "default"
    expected_answer_shape: str = "answer_first"
    language_hint: str = "unknown"

    def to_rule(self) -> RuleDecision:
        return RuleDecision(
            route=self.route,
            confidence=self.confidence,
            reason=self.reason,
            matched_rules=[
                "global_hybrid_router",
                f"semantic_intent:{self.normalized_intent}",
                f"source_policy:{self.source_policy}",
            ],
            high_stakes=self.high_stakes,
            signals=self.to_dict(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "normalized_query": self.normalized_query,
            "normalized_intent": self.normalized_intent,
            "entities": list(self.entities),
            "freshness_required": self.freshness_required,
            "official_preferred": self.official_preferred,
            "high_stakes": self.high_stakes,
            "preferred_tools": list(self.preferred_tools),
            "source_policy": self.source_policy,
            "expected_answer_shape": self.expected_answer_shape,
            "language_hint": self.language_hint,
        }


class LanguageAgnosticIntentNormalizer:
    """Converts raw text into language-independent routing signals."""

    def normalize(self, query: str) -> Dict[str, Any]:
        raw = str(query or "").strip()
        compact = re.sub(r"\s+", " ", raw).strip()
        asciiish = self._asciiish(compact)
        language_hint = self._language_hint(compact)
        entities = self._extract_entities(compact, asciiish)
        package_entity = self._package_entity(compact, asciiish, entities)
        freshness_required = bool(FRESHNESS_MARKERS.search(asciiish)) or any(
            marker in compact.lower() for marker in MULTILINGUAL_VERSION_HINTS
        )
        official_preferred = bool(OFFICIAL_MARKERS.search(asciiish))
        high_stakes = bool(HIGH_STAKES_MARKERS.search(asciiish))
        if package_entity:
            freshness_required = True
            official_preferred = True
        return {
            "raw_query": raw,
            "normalized_query": asciiish or compact,
            "language_hint": language_hint,
            "entities": entities,
            "package_entity": package_entity,
            "freshness_required": freshness_required,
            "official_preferred": official_preferred,
            "high_stakes": high_stakes,
        }

    def _asciiish(self, text: str) -> str:
        replacements = {
            "dernière": "derniere",
            "versión": "version",
            "última": "ultima",
            "क्या": "kya",
            "है": "hai",
            "का": "ka",
            "की": "ki",
            "最新版本": " latest version ",
            "最新版": " latest version ",
            "是多少": " what is ",
            "？": "?",
        }
        out = str(text or "")
        for src, dst in replacements.items():
            out = out.replace(src, dst)
        return re.sub(r"\s+", " ", out).strip().lower()

    def _language_hint(self, text: str) -> str:
        if re.search(r"[\u4e00-\u9fff]", text):
            return "cjk"
        if re.search(r"[\u0900-\u097f]", text):
            return "indic"
        if re.search(r"\b(quoi|quelle|derniere|dernière|version)\b", text, re.I):
            return "latin_multilingual"
        if re.search(r"\b(kya|hai|ka|ki|oda|enna|macha|latest)\b", text, re.I):
            return "transliterated"
        return "english_or_unknown"

    def _extract_entities(self, raw: str, normalized: str) -> List[str]:
        candidates = re.findall(r"\b[A-Za-z][A-Za-z0-9_.-]{1,40}\b", raw)
        if not candidates and re.search(r"[\u4e00-\u9fff]", raw):
            candidates = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,40}", raw)
        entities: List[str] = []
        for item in candidates:
            key = item.strip("?.!,;:").lower()
            if not key or key in STOP_ENTITIES:
                continue
            if key in STOP_ENTITIES:
                continue
            if key not in {e.lower() for e in entities}:
                entities.append(item.strip("?.!,;:"))
        return entities[:5]

    def _package_entity(self, raw: str, normalized: str, entities: List[str]) -> Optional[str]:
        combined = f"{normalized} {raw}".strip()
        has_version_hint = bool(re.search(r"\b(version|release)\b", normalized, re.I)) or any(
            marker in combined.lower() for marker in MULTILINGUAL_VERSION_HINTS
        )
        if not has_version_hint:
            return None
        for pattern in PACKAGE_VERSION_PATTERNS:
            match = pattern.search(combined)
            if not match:
                continue
            entity = str(match.group("entity") or "").strip("?.!,;:").lower()
            if entity and entity not in STOP_ENTITIES:
                return "next" if entity in {"nextjs", "next.js"} else entity
        if entities:
            entity = entities[-1].lower()
            return "next" if entity in {"nextjs", "next.js"} else entity
        return None


class GlobalHybridRouter:
    """Hybrid semantic router: deterministic fast hints stay first, this handles meaning-level routing."""

    def __init__(self) -> None:
        self._normalizer = LanguageAgnosticIntentNormalizer()

    def route(self, query: str, context: Optional[Dict[str, Any]] = None) -> RuleDecision:
        context = dict(context or {})
        universal = context.get("universal_understanding")
        if isinstance(universal, dict):
            route_hint = str(universal.get("route_hint") or "").strip().lower()
            confidence = float(universal.get("confidence") or 0.0)
            if route_hint and confidence >= 0.68:
                signals = {
                    "raw_query": str(universal.get("original_query") or query),
                    "normalized_query": str(universal.get("normalized_query") or query),
                    "language_hint": str(universal.get("language_hint") or "unknown"),
                    "entities": list((universal.get("entities") or {}).values()) if isinstance(universal.get("entities"), dict) else [],
                    "freshness_required": route_hint in {"fast_search", "news_search"},
                    "official_preferred": bool((universal.get("query_plan_summary") or {}).get("lanes", {}).get("official"))
                    if isinstance(universal.get("query_plan_summary"), dict)
                    else False,
                    "high_stakes": False,
                }
                return self._signal(
                    signals,
                    route=route_hint,
                    intent=str(universal.get("intent_hint") or "universal_understanding"),
                    confidence=confidence,
                    reason="global_router_universal_understanding_hint",
                    tools=[],
                    source_policy="universal_understanding",
                ).to_rule()
        signals = self._normalizer.normalize(query)
        text = str(signals.get("normalized_query") or "").strip()
        raw = str(signals.get("raw_query") or "").strip()
        entities = list(signals.get("entities") or [])
        package_entity = signals.get("package_entity")
        freshness = bool(signals.get("freshness_required"))
        official = bool(signals.get("official_preferred"))
        high_stakes = bool(signals.get("high_stakes"))

        if bool(context.get("has_active_doc")) or DOC_MARKERS.search(text):
            return self._signal(
                signals,
                route="doc_mode",
                intent="document_qa",
                confidence=0.91,
                reason="hybrid_document_context",
                tools=["document_retriever"],
                source_policy="uploaded_document",
            ).to_rule()
        if package_entity:
            return self._signal(
                signals,
                route="fast_search",
                intent="package_lookup",
                confidence=0.94,
                reason="hybrid_package_version_lookup",
                entities=[str(package_entity)],
                tools=["npm_registry_lookup", "pypi_registry_lookup"],
                source_policy="registry_first",
            ).to_rule()
        if SMALLTALK_MARKERS.fullmatch(text):
            return self._signal(
                signals,
                route="fast_message",
                intent="smalltalk",
                confidence=0.93,
                reason="hybrid_smalltalk",
                tools=[],
                source_policy="no_source_needed",
            ).to_rule()
        if high_stakes:
            return self._signal(
                signals,
                route="official_search",
                intent="high_stakes_research",
                confidence=0.88,
                reason="hybrid_high_stakes_official_required",
                tools=["official_web_search", "web_extract", "source_ranker"],
                source_policy="official_first",
            ).to_rule()
        if NEWS_MARKERS.search(text) and freshness:
            return self._signal(
                signals,
                route="news_search",
                intent="news_search",
                confidence=0.88,
                reason="hybrid_fresh_news",
                tools=["general_web_search", "web_extract", "source_ranker"],
                source_policy="fresh_reporting_plus_official",
            ).to_rule()
        if COMPARISON_MARKERS.search(text):
            return self._signal(
                signals,
                route="comparison_search",
                intent="comparison_research",
                confidence=0.86,
                reason="hybrid_comparison_research",
                tools=["official_web_search", "general_web_search", "web_extract", "source_ranker"],
                source_policy="official_plus_diverse_independent",
            ).to_rule()
        if official:
            return self._signal(
                signals,
                route="official_search",
                intent="official_source_lookup",
                confidence=0.86,
                reason="hybrid_official_source_lookup",
                tools=["official_web_search", "web_extract", "source_ranker"],
                source_policy="official_first",
            ).to_rule()
        if RESEARCH_MARKERS.search(text):
            return self._signal(
                signals,
                route="deep_search",
                intent="deep_research",
                confidence=0.84,
                reason="hybrid_deep_research",
                tools=["general_web_search", "web_extract", "source_ranker"],
                source_policy="quality_ranked_multi_source",
            ).to_rule()
        if freshness:
            return self._signal(
                signals,
                route="fast_search",
                intent="standard_web_search",
                confidence=0.80,
                reason="hybrid_standard_current_lookup",
                tools=["general_web_search"],
                source_policy="fresh_source_preferred",
            ).to_rule()
        if TASK_MARKERS.search(text):
            return self._signal(
                signals,
                route="task",
                intent="task_execution",
                confidence=0.76,
                reason="hybrid_task_execution",
                tools=["code_executor"],
                source_policy="tool_result",
            ).to_rule()

        return self._signal(
            signals,
            route="no_search",
            intent="simple_explanation",
            confidence=0.62,
            reason="hybrid_general_no_search",
            tools=[],
            source_policy="no_source_needed",
        ).to_rule()

    def _signal(
        self,
        base: Dict[str, Any],
        *,
        route: str,
        intent: str,
        confidence: float,
        reason: str,
        tools: List[str],
        source_policy: str,
        entities: Optional[List[str]] = None,
    ) -> HybridRouteSignal:
        return HybridRouteSignal(
            normalized_query=str(base.get("normalized_query") or ""),
            normalized_intent=intent,
            route=route,
            confidence=confidence,
            reason=reason,
            entities=list(entities if entities is not None else base.get("entities") or []),
            freshness_required=bool(base.get("freshness_required")),
            official_preferred=bool(base.get("official_preferred")),
            high_stakes=bool(base.get("high_stakes")),
            preferred_tools=tools,
            source_policy=source_policy,
            language_hint=str(base.get("language_hint") or "unknown"),
        )
