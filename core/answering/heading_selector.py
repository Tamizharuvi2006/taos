from __future__ import annotations

import re


def select_schema_key(
    *,
    intent: str = "",
    route: str = "",
    answer_mode: str = "",
    evidence_state: str = "",
    query: str = "",
) -> str:
    normalized_intent = str(intent or "").strip().lower()
    normalized_route = str(route or "").strip().lower()
    normalized_mode = str(answer_mode or "").strip().lower()
    normalized_state = str(evidence_state or "").strip().lower()
    text = str(query or "").strip().lower()

    if normalized_mode == "no_usable_evidence" or normalized_state == "no_usable_evidence":
        return "no_usable_evidence"
    if normalized_mode == "related_evidence_only" or normalized_state == "related_evidence_only":
        return "related_evidence_only"
    if normalized_intent == "rumour_verification" or re.search(r"\b(rumou?r|is it true|heard news|claim)\b", text):
        return "rumour_verification"
    if normalized_route == "fast_search" and re.search(r"\b(version|latest version|npm)\b", text):
        return "package_version"
    if normalized_route == "comparison_search" or normalized_intent == "comparison":
        return "comparison"
    if normalized_route in {"task", "standard_task"} and re.search(r"\b(error|failed|fix|not working|bug)\b", text):
        return "troubleshooting"
    if normalized_route == "no_search" or normalized_intent in {"general_research", "explanation"}:
        return "explanation"
    if re.search(r"\b(ceo|founder|owner|who is)\b", text):
        return "entity_lookup"
    if re.search(r"\b(profile|twitter|x.com|linkedin|instagram)\b", text):
        return "social_profile"
    if re.search(r"\b(scam|legit|legitimacy|safe or not)\b", text):
        return "legitimacy_check"
    return "default_research"
