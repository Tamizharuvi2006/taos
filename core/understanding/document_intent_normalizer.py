from __future__ import annotations

import re
from typing import Dict


class DocumentIntentNormalizer:
    """Extracts document-mode hints from messy study/upload questions."""

    DOC_RE = re.compile(r"\b(pdf|document|doc|uploaded|file|notes|chapter|unit|this\s+pdf|this\s+document)\b", re.I)
    MARK_RE = re.compile(r"\b(?P<marks>1|2|5|10|12|15|16|20)\s*(?:mark|marks|mrk|mrks)\b", re.I)
    IMPORTANT_RE = re.compile(r"\b(important|imprtnt|impt|imp|exam|question|questions|qns)\b", re.I)

    def normalize(self, query: str, *, normalized_query: str = "") -> Dict[str, object]:
        raw = str(query or "")
        text = str(normalized_query or raw)
        if not (self.DOC_RE.search(text) or self.DOC_RE.search(raw)):
            return {}
        mark_match = self.MARK_RE.search(text) or self.MARK_RE.search(raw)
        hints: Dict[str, object] = {
            "document_context": True,
            "mode": "document_qa",
        }
        if self.IMPORTANT_RE.search(text) or self.IMPORTANT_RE.search(raw):
            hints["mode"] = "important_questions"
        if mark_match:
            marks = str(mark_match.group("marks"))
            hints["mark_format"] = f"{marks}_mark"
        return hints


def normalize_document_intent(query: str, *, normalized_query: str = "") -> Dict[str, object]:
    return DocumentIntentNormalizer().normalize(query, normalized_query=normalized_query)
