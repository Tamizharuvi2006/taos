from __future__ import annotations

import re
from typing import Dict, Tuple

from .intent_frame import IntentFrame


class RouteHintBuilder:
    """Builds route hints from the universal understanding frame."""

    FAST_MESSAGE_RE = re.compile(r"^\s*(hey|hi|hello|yo|thanks|ok|bye)(?:\s+(buddy|bro|da|macha|dude))?\s*$", re.I)
    EXPLAIN_RE = re.compile(r"\b(explain|define|meaning|what\s+is|how\s+does|na\s+enna)\b", re.I)
    NEWS_RE = re.compile(r"\b(news|headline|breaking|update|updates|current\s+status)\b", re.I)
    PERSONAL_SUPPORT_RE = re.compile(
        r"\b("
        r"break\s*up|breakup|broke\s+up|heart\s*break|heartbreak|"
        r"feeling\s+sad|feel\s+sad|feel\s+bad|feeling\s+low|"
        r"depressed|lonely|crying|anxious|stress(?:ed)?|"
        r"my\s+girlfriend|my\s+boyfriend|relationship|got\s+dumped"
        r")\b",
        re.I,
    )

    def build(self, frame: IntentFrame) -> Tuple[str, str, float]:
        text = str(frame.normalized_query or frame.cleaned_query or frame.original_query or "").strip()
        lower = text.lower()
        if self.FAST_MESSAGE_RE.fullmatch(text):
            return "fast_message", "universal_fast_message", 0.94
        if self.PERSONAL_SUPPORT_RE.search(lower):
            return "no_search", "universal_personal_support", 0.92
        if frame.document_hints:
            return "doc_mode", "universal_document_intent", 0.92
        if frame.task_hints:
            return "task", "universal_task_intent", 0.9
        if frame.relation == "latest_version":
            return "fast_search", "universal_package_version_lookup", 0.94
        if frame.intent == "explanation" or self.EXPLAIN_RE.search(lower):
            return "no_search", "universal_explanation", 0.86
        if frame.entity_intelligence_summary:
            if "missing_entity" in set(frame.entity_intelligence_summary.get("ambiguity_flags") or ()):
                return "clarification", "entity_intelligence_missing_entity", 0.74
            entity_intent = str(frame.entity_intelligence_summary.get("intent") or "")
            if entity_intent in {
                "ceo_lookup",
                "founder_lookup",
                "linkedin_profile",
                "official_social_profile",
                "company_details",
                "legitimacy_check",
            }:
                return "entity_lookup", "entity_intelligence_entity_profile", 0.9
            return "entity_lookup", "entity_intelligence_public_research", 0.88
        if frame.intent == "rumour_verification":
            return "news_search", "universal_rumour_verification", 0.9
        if frame.intent == "official_verification":
            return "official_search", "universal_official_verification", 0.88
        if frame.intent == "comparison" or re.search(r"\b(vs|versus|better|compare|comparison)\b", lower):
            return "comparison_search", "universal_comparison", 0.88
        if frame.code_hints:
            return "task", "universal_code_help_intent", 0.84
        if self.NEWS_RE.search(lower) and re.search(r"\b(current|latest|today|now|recent|breaking)\b", lower):
            return "news_search", "universal_news_lookup", 0.88
        if frame.intent == "current_lookup" or re.search(r"\b(current|latest|version|price|today|now)\b", lower):
            return "fast_search", "universal_current_lookup", 0.84
        if set(frame.ambiguity_flags or ()) & {"low_context_reference", "subject_ambiguity"}:
            return "clarification", "low_confidence_ambiguous_input", 0.72
        if "low_confidence" in set(frame.ambiguity_flags or ()) and len(lower.split()) <= 2:
            return "clarification", "low_confidence_ambiguous_input", 0.72
        if float(frame.confidence or 0.0) < 0.32 and len(lower.split()) <= 3:
            return "clarification", "universal_low_signal", 0.68
        return "", "", 0.0


def build_route_hint(frame: IntentFrame) -> Dict[str, object]:
    route, reason, confidence = RouteHintBuilder().build(frame)
    return {"route_hint": route, "reason": reason, "confidence": confidence}
