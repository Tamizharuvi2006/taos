from __future__ import annotations

import re
from typing import Dict


class CodeHelpNormalizer:
    """Extracts coding-help hints without executing code or changing task behavior."""

    CODE_RE = re.compile(r"\b(fix|debug|error|module|package|import|react|angular|vite|npm|node|python)\b", re.I)
    MODULE_NOT_FOUND_RE = re.compile(r"\b(module|package|import)\s+(?:not\s+)?found\b|\bnot\s+found\b", re.I)

    def normalize(self, query: str, *, normalized_query: str = "") -> Dict[str, object]:
        raw = str(query or "")
        text = str(normalized_query or raw)
        if not (self.CODE_RE.search(text) or self.CODE_RE.search(raw)):
            return {}
        hints: Dict[str, object] = {"category": "code_help"}
        if self.MODULE_NOT_FOUND_RE.search(text):
            hints["error_type"] = "module_not_found"
        framework = _framework(text)
        if framework:
            hints["framework"] = framework
        if hints.get("error_type") or framework:
            hints["normalized_issue"] = text
        return hints


def _framework(text: str) -> str:
    lower = str(text or "").lower()
    if "react" in lower:
        return "React"
    if "angular" in lower:
        return "Angular"
    if "vite" in lower:
        return "Vite"
    return ""


def normalize_code_help(query: str, *, normalized_query: str = "") -> Dict[str, object]:
    return CodeHelpNormalizer().normalize(query, normalized_query=normalized_query)
