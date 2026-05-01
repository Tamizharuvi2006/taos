from __future__ import annotations

import re
from dataclasses import dataclass

from .memory_policy import looks_like_secret
from .sensitive_memory_filter import check_sensitive_memory


UNSAFE_INSTRUCTION_PATTERNS = (
    re.compile(r"\bignore (?:previous|all|system|developer) instructions\b", re.I),
    re.compile(r"\b(?:reveal|leak|show|print)\b.{0,40}\b(?:system prompt|developer prompt|internal|secrets?)\b", re.I),
    re.compile(r"\b(?:bypass|disable)\b.{0,40}\b(?:safety|policy|auth|permissions)\b", re.I),
)


@dataclass(frozen=True)
class ImportPolicyDecision:
    allowed: bool
    risk: str
    reason: str
    blocked_type: str = ""


def evaluate_import_line(text: str, *, explicit_sensitive_ok: bool = False) -> ImportPolicyDecision:
    value = str(text or "")
    if any(pattern.search(value) for pattern in UNSAFE_INSTRUCTION_PATTERNS):
        return ImportPolicyDecision(
            allowed=False,
            risk="blocked",
            reason="Instruction-injection or internal-leak request cannot be imported as memory.",
            blocked_type="unsafe_instruction",
        )
    if looks_like_secret(value):
        return ImportPolicyDecision(False, "blocked", "Secrets/API keys/passwords cannot be imported.", "secret")
    sensitive = check_sensitive_memory(value, explicit_user_request=explicit_sensitive_ok)
    if not sensitive.allowed and sensitive.category == "secret":
        return ImportPolicyDecision(False, "blocked", sensitive.reason, "secret")
    if not sensitive.allowed:
        return ImportPolicyDecision(False, "sensitive", sensitive.reason, "sensitive")
    return ImportPolicyDecision(True, "low", "Safe to show in review.")
