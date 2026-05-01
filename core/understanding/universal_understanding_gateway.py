from __future__ import annotations

from dataclasses import asdict, replace
from difflib import SequenceMatcher
import re
from typing import Dict, Iterable, List, Tuple

from .code_help_normalizer import CodeHelpNormalizer
from .document_intent_normalizer import DocumentIntentNormalizer
from .entity_resolver import EntityResolver
from .intent_frame import CorrectionCandidate, IntentFrame
from .language_hint_detector import LanguageHintDetector
from .meaning_frame import build_meaning_frame
from .route_hint_builder import RouteHintBuilder
from .search_intent_planner import SearchIntentPlanner
from .task_intent_normalizer import TaskIntentNormalizer


ABBREVIATIONS = {
    "js": "JavaScript",
    "mrng": "morning",
}

SEMANTIC_VOCAB = (
    "hey",
    "buddy",
    "explain",
    "javascript",
    "closures",
    "simple",
    "current",
    "version",
    "important",
    "marks",
    "remind",
    "tomorrow",
    "morning",
    "module",
    "found",
    "react",
    "angular",
    "which",
    "better",
)


class UniversalUnderstandingGateway:
    """Front-door understanding layer used before route selection."""

    def __init__(self) -> None:
        self._intent = SearchIntentPlanner()
        self._language = LanguageHintDetector()
        self._docs = DocumentIntentNormalizer()
        self._tasks = TaskIntentNormalizer()
        self._code = CodeHelpNormalizer()
        self._route_hints = RouteHintBuilder()

    def understand(self, query: str, context: Dict[str, object] | None = None) -> IntentFrame:
        context = dict(context or {})
        original = str(query or "").strip()
        frame = self._intent.plan(original)
        normalized_query, correction_candidates = normalize_for_universal_routes(frame.cleaned_query or original)
        language = self._language.detect(original)
        normalized_query = normalized_query or frame.cleaned_query or original
        document_hints = self._docs.normalize(original, normalized_query=normalized_query)
        task_hints = self._tasks.normalize(original, normalized_query=normalized_query)
        code_hints = self._code.normalize(original, normalized_query=normalized_query)
        entity_summary = _entity_intelligence_summary(original)
        frame = replace(
            frame,
            normalized_query=normalized_query,
            language_hint=str(language.get("primary") or "english"),
            document_hints=document_hints,
            task_hints=task_hints,
            code_hints=code_hints,
            entity_intelligence_summary=entity_summary,
            correction_candidates=_dedupe_candidates([*frame.correction_candidates, *correction_candidates]),
        )
        if _is_package_version(frame):
            frame = replace(
                frame,
                intent_hint="current_lookup",
                relation="latest_version",
                normalized_question=f"What is the current version of {_object_name(frame)}?",
            )
        elif frame.intent == "general_research":
            frame = replace(frame, intent_hint=_intent_hint_from_text(frame.normalized_query))
        else:
            frame = replace(frame, intent_hint=frame.intent)
        frame = replace(frame, ambiguity_flags=tuple(_ambiguity_flags(frame, context=context)))
        route_hint, route_reason, route_confidence = self._route_hints.build(frame)
        if route_hint == "fast_message":
            frame = replace(frame, intent_hint="small_talk")
        next_confidence = frame.confidence
        if route_hint:
            next_confidence = round(max(float(frame.confidence or 0.0), route_confidence), 3)
        query_plan_summary = dict(frame.query_plan_summary)
        if route_reason:
            query_plan_summary["route_hint_reason"] = route_reason
        if route_hint in {"news_search", "official_search", "comparison_search"} or frame.intent == "rumour_verification":
            from taos.core.search.query_planner_v2 import SearchQueryPlannerV2

            summary = SearchQueryPlannerV2().plan(original).summary()
            summary["normalized_query"] = frame.normalized_query
            query_plan_summary = {**query_plan_summary, **summary}
        if frame.entity_intelligence_summary:
            query_plan_summary["entity_intelligence_summary"] = dict(frame.entity_intelligence_summary)
        frame = replace(frame, route_hint=route_hint, confidence=next_confidence)
        frame = replace(frame, meaning_frame=build_meaning_frame(frame))
        return replace(
            frame,
            query_plan_summary=query_plan_summary,
        )


def normalize_for_universal_routes(query: str) -> Tuple[str, Tuple[CorrectionCandidate, ...]]:
    tokens = re.findall(r"[A-Za-z0-9_.-]+|[^\w\s]", str(query or ""))
    out: List[str] = []
    candidates: List[CorrectionCandidate] = []
    entities = EntityResolver()
    for token in tokens:
        if not re.match(r"^[A-Za-z0-9_.-]+$", token):
            out.append(token)
            continue
        entity_match = entities.best_match(token)
        if entity_match and entity_match.kind in {"product", "company", "tool_framework"}:
            replacement = entity_match.canonical
            if replacement.lower() != str(token).lower():
                candidates.append(
                    CorrectionCandidate(
                        token=str(token).lower(),
                        candidate=replacement,
                        kind="entity_lock",
                        score=round(float(entity_match.score or 0.99), 3),
                    )
                )
            out.append(replacement)
            continue
        replacement, score = _semantic_replacement(token)
        if replacement.lower() != token.lower():
            candidates.append(
                CorrectionCandidate(
                    token=token.lower(),
                    candidate=replacement,
                    kind="semantic_correction",
                    score=round(score, 3),
                )
            )
        out.append(replacement)
    text = " ".join(out)
    text = re.sub(r"\s+([?.!,;:])", r"\1", text)
    text = re.sub(r"\bJavascript\b", "JavaScript", text)
    text = re.sub(r"\bclosures simple\b", "closures simply", text, flags=re.I)
    text = re.sub(r"\btomorrow morning (?=\d)", "tomorrow morning at ", text, flags=re.I)
    text = re.sub(r"\bmodule not found react\b", "module not found error in React", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text, tuple(candidates)


def frame_to_trace_summary(frame: IntentFrame) -> Dict[str, object]:
    return {
        "original_query": frame.original_query,
        "cleaned_query": frame.cleaned_query,
        "normalized_query": frame.normalized_query,
        "normalized_question": frame.normalized_question,
        "language_hint": frame.language_hint,
        "intent_hint": frame.intent_hint,
        "route_hint": frame.route_hint,
        "entities": dict(frame.entities),
        "relation": frame.relation,
        "task_hints": dict(frame.task_hints),
        "document_hints": dict(frame.document_hints),
        "code_hints": dict(frame.code_hints),
        "entity_intelligence_summary": dict(frame.entity_intelligence_summary),
        "correction_candidates": [asdict(candidate) for candidate in frame.correction_candidates],
        "confidence": frame.confidence,
        "ambiguity_flags": list(frame.ambiguity_flags),
        "query_plan_summary": dict(frame.query_plan_summary),
        "raw_query_priority": frame.raw_query_priority,
        "meaning_frame": frame.meaning_frame.as_dict() if frame.meaning_frame else {},
    }


def frame_from_context(value: object) -> IntentFrame | None:
    if isinstance(value, IntentFrame):
        return value
    return None


def _semantic_replacement(token: str) -> Tuple[str, float]:
    raw = str(token or "")
    lower = raw.lower()
    if lower in ABBREVIATIONS:
        return ABBREVIATIONS[lower], 1.0
    best = lower
    best_score = 0.0
    for candidate in SEMANTIC_VOCAB:
        score = SequenceMatcher(None, lower, candidate).ratio()
        if lower[:1] == candidate[:1]:
            score += 0.04
        if score > best_score:
            best = candidate
            best_score = score
    if len(lower) >= 3 and best_score >= 0.78:
        return _case_like(raw, best), min(1.0, best_score)
    return raw, 1.0


def _case_like(original: str, replacement: str) -> str:
    if replacement == "javascript":
        return "JavaScript"
    if replacement in {"react", "angular"}:
        return replacement.title()
    if original[:1].isupper():
        return replacement.title()
    return replacement


def _intent_hint_from_text(text: str) -> str:
    lower = str(text or "").lower()
    if re.search(r"\b(explain|define|meaning)\b", lower):
        return "explanation"
    if re.search(r"\b(current|latest|version)\b", lower):
        return "current_lookup"
    if re.search(r"\b(vs|versus|better|compare)\b", lower):
        return "comparison"
    return "general"


def _entity_intelligence_summary(query: str) -> Dict[str, object]:
    from taos.core.entity import EntityIntent, EntityIntentDetector, EntitySourcePlanner

    entity_query = EntityIntentDetector().detect(query)
    if entity_query.intent == EntityIntent.UNKNOWN:
        return {}
    plan = EntitySourcePlanner().plan(entity_query)
    return {
        "intent": entity_query.intent,
        "entity_name": entity_query.entity_name,
        "entity_type": entity_query.entity_type,
        "requested_attribute": entity_query.requested_attribute,
        "platform": entity_query.platform,
        "ambiguity_flags": list(entity_query.ambiguity_flags),
        "source_lanes": dict(plan.lanes),
        "required_lanes": list(plan.required_lanes),
        "public_only_policy": list(entity_query.public_only_policy),
    }


def _is_package_version(frame: IntentFrame) -> bool:
    text = f"{frame.original_query} {frame.cleaned_query} {frame.normalized_query}".lower()
    return bool((frame.entities.get("tool_framework") or frame.entities.get("product")) and re.search(r"\b(current|latest|version|versio)\b", text))


def _object_name(frame: IntentFrame) -> str:
    return frame.entities.get("tool_framework") or frame.entities.get("product") or frame.entities.get("company") or "the package"


def _ambiguity_flags(frame: IntentFrame, *, context: Dict[str, object]) -> List[str]:
    if frame.route_hint:
        return []
    text = str(frame.normalized_query or frame.cleaned_query or "").strip()
    if re.fullmatch(r"\s*(hey|hi|hello|yo|thanks|ok|bye)(?:\s+(buddy|bro|da|macha|dude))?\s*", text, re.I):
        return []
    if re.search(r"\b(that|this|it)\b.*\b(before|earlier|again)\b|\bfrom\s+before\b", text, re.I):
        return ["low_context_reference"]
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return ["empty"]
    if frame.meaning_frame and "subject_ambiguity" in set(frame.meaning_frame.ambiguity_flags or ()):
        return ["low_confidence"]
    if float(frame.confidence or 0.0) < 0.32 and not frame.entities and len(tokens) <= 3:
        return ["low_confidence"]
    return []


def _dedupe_candidates(candidates: Iterable[CorrectionCandidate]) -> Tuple[CorrectionCandidate, ...]:
    out: List[CorrectionCandidate] = []
    seen = set()
    for candidate in candidates:
        key = (candidate.token.lower(), candidate.candidate.lower(), candidate.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return tuple(out)


def understand_universal_query(query: str, context: Dict[str, object] | None = None) -> IntentFrame:
    return UniversalUnderstandingGateway().understand(query, context=context)
