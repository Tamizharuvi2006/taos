from __future__ import annotations

import re
from typing import Dict, Iterable, Tuple

from .intent_frame import IntentFrame, MeaningFrame


def build_meaning_frame(frame: IntentFrame) -> MeaningFrame:
    text = " ".join(
        [
            str(frame.original_query or ""),
            str(frame.cleaned_query or ""),
            str(frame.normalized_query or ""),
            str(frame.normalized_question or ""),
        ]
    ).lower()
    product = str(frame.entities.get("product") or frame.entities.get("company") or frame.object or "").strip()
    protected = _protected_entities(frame)
    drifts = _disallowed_drifts(frame=frame, protected_entities=protected, text=text)
    claim_type = _claim_type(frame)
    target_attribute = _target_attribute(frame)
    flags = list(frame.ambiguity_flags or ())
    if _looks_claude_like(text) and "anthropic" not in text and "model" not in text and "ai" not in text and "news" not in text:
        flags.append("subject_ambiguity")
    return MeaningFrame(
        original_query=frame.original_query,
        normalized_query=frame.normalized_question or frame.normalized_query or frame.cleaned_query or frame.original_query,
        user_intent=frame.intent,
        route_hint=frame.route_hint,
        claim_type=claim_type,
        primary_subject=product,
        protected_entities=protected,
        relation=frame.relation,
        target_attribute=target_attribute,
        disallowed_subject_drifts=drifts,
        ambiguity_flags=tuple(_dedupe(flags)),
        confidence=float(frame.confidence or 0.0),
    )


def detect_subject_drift(answer: str, meaning_frame: MeaningFrame | Dict[str, object] | None) -> bool:
    frame = _coerce_frame(meaning_frame)
    if not frame or not frame.primary_subject:
        return False
    text = str(answer or "").lower()
    protected = {item.lower() for item in frame.protected_entities}
    mentions_protected = any(entity and entity in text for entity in protected)
    mentions_drift = any(drift.lower() in text for drift in frame.disallowed_subject_drifts)
    if mentions_drift and not mentions_protected:
        return True
    return False


def enforce_meaning_frame(answer: str, meaning_frame: MeaningFrame | Dict[str, object] | None) -> tuple[str, bool]:
    frame = _coerce_frame(meaning_frame)
    if not frame:
        return str(answer or ""), False
    if not detect_subject_drift(answer, frame):
        return str(answer or ""), False
    subject = frame.primary_subject or "the requested subject"
    normalized = frame.normalized_query or frame.original_query or subject
    safe = (
        f"I could not confirm the claim about {subject}.\n\n"
        f"The current request is about: {normalized}\n\n"
        f"I do not have enough verified evidence in this run to treat the claim as confirmed, "
        f"and I will not reinterpret {subject} as a different topic."
    )
    return safe, True


def _protected_entities(frame: IntentFrame) -> Tuple[str, ...]:
    out = []
    for item in (
        frame.entities.get("product"),
        frame.entities.get("company"),
        frame.entities.get("tool_framework"),
    ):
        text = str(item or "").strip()
        if text:
            out.append(text)
    if any(str(item).lower() == "claude" for item in out):
        out.append("Anthropic")
    return tuple(_dedupe(out))


def _disallowed_drifts(*, frame: IntentFrame, protected_entities: Iterable[str], text: str) -> Tuple[str, ...]:
    protected = {str(item).lower() for item in protected_entities if str(item).strip()}
    disallowed = []
    if "claude" in protected and any(token in text for token in ("model", "issue", "news", "anthropic", "ai", "claudde", "claud", "cluade")):
        disallowed.extend(["cloud services", "cloud computing"])
    return tuple(_dedupe(disallowed))


def _claim_type(frame: IntentFrame) -> str:
    if frame.relation == "blocked_or_restricted_access":
        return "access_block_claim"
    if frame.relation == "unavailable_or_outage":
        return "availability_claim"
    if frame.relation == "latest_version":
        return "package_version_lookup"
    return frame.intent or "general"


def _target_attribute(frame: IntentFrame) -> str:
    if frame.relation == "latest_version":
        return "version"
    if frame.relation in {"blocked_or_restricted_access", "unavailable_or_outage"}:
        return "availability"
    return ""


def _looks_claude_like(text: str) -> bool:
    return bool(re.search(r"\b(claude|claudde|claud|cluade)\b", str(text or "").lower()))


def _dedupe(values: Iterable[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _coerce_frame(meaning_frame: MeaningFrame | Dict[str, object] | None) -> MeaningFrame | None:
    if isinstance(meaning_frame, MeaningFrame):
        return meaning_frame
    if not isinstance(meaning_frame, dict):
        return None
    return MeaningFrame(
        original_query=str(meaning_frame.get("original_query") or ""),
        normalized_query=str(meaning_frame.get("normalized_query") or ""),
        user_intent=str(meaning_frame.get("user_intent") or ""),
        route_hint=str(meaning_frame.get("route_hint") or ""),
        claim_type=str(meaning_frame.get("claim_type") or ""),
        primary_subject=str(meaning_frame.get("primary_subject") or ""),
        protected_entities=tuple(str(item) for item in meaning_frame.get("protected_entities") or [] if str(item).strip()),
        relation=str(meaning_frame.get("relation") or ""),
        target_attribute=str(meaning_frame.get("target_attribute") or ""),
        disallowed_subject_drifts=tuple(
            str(item) for item in meaning_frame.get("disallowed_subject_drifts") or [] if str(item).strip()
        ),
        ambiguity_flags=tuple(str(item) for item in meaning_frame.get("ambiguity_flags") or [] if str(item).strip()),
        confidence=float(meaning_frame.get("confidence") or 0.0),
    )
