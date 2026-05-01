"""Public trace and error redaction utilities."""

from __future__ import annotations

import re
from typing import Any


REDACTED = "[redacted]"

_SECRET_PATTERNS = [
    re.compile(r"(?i)(openrouter|serper|firebase|razorpay|telegram|zeptomail|whatsapp)[_\-\s]*(api[_\-\s]*)?(key|secret|token)\s*[:=]\s*['\"]?[^'\"\s,}]+"),
    re.compile(r"(?i)(api[_\-\s]*key|secret|token|private[_\-\s]*key|authorization)\s*[:=]\s*['\"]?[^'\"\s,}]+"),
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]{12,}"),
    re.compile(r"sk-[a-zA-Z0-9_\-]{12,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
]

_INTERNAL_PATTERNS = [
    re.compile(r"Traceback \(most recent call last\):.*", re.DOTALL),
    re.compile(r"[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)+[^\\/:*?\"<>|\r\n]*"),
    re.compile(r"(?i)(?:^|\s)(?:\.env|env file|firebase credentials|provider payload)(?:\s|$).*"),
]

_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|authorization|credential|private[_-]?key|password|traceback|stack|raw[_-]?request|raw[_-]?response|payload)"
)


def redact_secret_text(value: Any) -> str:
    """Return a public-safe string with secrets and raw internals removed."""

    text = str(value or "")
    if not text:
        return ""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    for pattern in _INTERNAL_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def sanitize_public_trace(value: Any) -> Any:
    """Recursively sanitize a trace-like object for public/API responses."""

    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _SENSITIVE_KEY_RE.search(key):
                cleaned[key] = REDACTED
                continue
            cleaned[key] = sanitize_public_trace(raw_value)
        return cleaned
    if isinstance(value, list):
        return [sanitize_public_trace(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_public_trace(item) for item in value)
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def contains_secret_leak(value: Any) -> bool:
    """Best-effort detector for tests and security audits."""

    text = str(value or "")
    if not text:
        return False
    lowered = text.lower()
    if "traceback (most recent call last)" in lowered:
        return True
    if re.search(r"[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)+", text):
        return True
    if re.search(r"(?i)(openrouter|serper|firebase|razorpay|telegram|zeptomail|whatsapp)[_\-\s]*(api[_\-\s]*)?(key|secret|token)\s*[:=]", text):
        return True
    if re.search(r"(?i)bearer\s+[a-z0-9._\-]{12,}", text):
        return True
    if re.search(r"sk-[a-zA-Z0-9_\-]{12,}", text):
        return True
    if "-----begin" in lowered and "private key-----" in lowered:
        return True
    return False
