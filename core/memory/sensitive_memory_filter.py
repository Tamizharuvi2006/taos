from __future__ import annotations

import re
from dataclasses import dataclass

from .memory_policy import looks_like_secret


SENSITIVE_PATTERNS = (
    re.compile(r"\b(?:ssn|aadhaar|passport|bank account|credit card)\b", re.I),
    re.compile(r"\b(?:medical diagnosis|health condition|religion|caste|sexual orientation)\b", re.I),
)


@dataclass(frozen=True)
class SensitiveMemoryResult:
    allowed: bool
    reason: str
    category: str = ""


def check_sensitive_memory(text: str, *, explicit_user_request: bool = False) -> SensitiveMemoryResult:
    value = str(text or "")
    if looks_like_secret(value):
        return SensitiveMemoryResult(False, "Secrets, API keys, passwords, and tokens are never saved.", "secret")
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(value):
            if explicit_user_request:
                return SensitiveMemoryResult(True, "Sensitive memory allowed because the user explicitly requested it.", "sensitive_explicit")
            return SensitiveMemoryResult(False, "Sensitive personal data is not saved automatically.", "sensitive")
    return SensitiveMemoryResult(True, "No sensitive memory risk detected.", "")
