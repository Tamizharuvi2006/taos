from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RecallQuery:
    query: str
    explicit_recall: bool
    project_hint: str = ""


def build_recall_query(text: str) -> RecallQuery:
    value = str(text or "").strip()
    lower = value.lower()
    explicit = any(token in lower for token in ("remember", "what did we decide", "what do you remember", "continue previous", "continue taos"))
    project_hint = "taos" if "taos" in lower else ""
    cleaned = re.sub(r"\b(what do you remember about|what did we decide about|remember|continue previous|continue)\b", " ", lower)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return RecallQuery(query=cleaned or value, explicit_recall=explicit, project_hint=project_hint)
