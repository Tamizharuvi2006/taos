from __future__ import annotations

from enum import Enum
from typing import Mapping


class AnswerStrategy(str, Enum):
    DIRECT_VERIFIED = "direct_verified"
    BEST_SUPPORTED = "best_supported"
    RUMOUR_UNCONFIRMED_WITH_CONTEXT = "rumour_unconfirmed_with_context"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    WEAK_CANDIDATE = "weak_candidate"
    NO_USABLE_EVIDENCE = "no_usable_evidence"


def choose_answer_strategy(*, intent: str, status: str = "", evidence: Mapping[str, object] | None = None, answer_mode: str = "") -> AnswerStrategy:
    evidence = dict(evidence or {})
    if intent == "rumour_verification" and status in {"not_confirmed", "unclear"} and evidence.get("related"):
        return AnswerStrategy.RUMOUR_UNCONFIRMED_WITH_CONTEXT
    if evidence.get("conflict_detected") or status == "partially_true":
        return AnswerStrategy.CONFLICTING_EVIDENCE
    if answer_mode == "verified" or status == "confirmed":
        return AnswerStrategy.DIRECT_VERIFIED
    if answer_mode in {"best_supported", "partial_but_useful"}:
        return AnswerStrategy.BEST_SUPPORTED
    if answer_mode == "weak_candidate":
        return AnswerStrategy.WEAK_CANDIDATE
    return AnswerStrategy.NO_USABLE_EVIDENCE
