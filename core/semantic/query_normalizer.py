from __future__ import annotations

import re


_RESEARCH_PREFIX_PATTERNS = (
    r"^\s*research\s+and\s+verify\s+with\s+current\s+sources\s*:\s*",
    r"^\s*comprehensive\s+research\s+and\s+verify\s+with\s+current\s+sources\s*:\s*",
    r"^\s*detailed\s+research\s+and\s+verify\s+with\s+current\s+sources\s*:\s*",
    r"^\s*comprehensive\s+research\s*:\s*",
    r"^\s*detailed\s+research\s*:\s*",
    r"^\s*detailed\s+answer\s*:\s*",
)


def strip_research_instruction_prefix(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text
    for pattern in _RESEARCH_PREFIX_PATTERNS:
        normalized = re.sub(pattern, "", normalized, flags=re.I)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_user_query(value: str) -> str:
    return strip_research_instruction_prefix(value)

