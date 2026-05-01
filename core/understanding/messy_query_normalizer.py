from __future__ import annotations

import re


FILLER_PATTERNS = (
    r"\bresearch\s+that\b",
    r"\bresearch\s+about\b",
    r"\bresearch\b",
    r"\bi\s+(?:got|heard|saw|read)\s+(?:an?\s+)?(?:news|rumou?r)\s+that\b",
    r"\bi\s+(?:got|heard|saw|read)\s+(?:an?\s+)?(?:news|rumou?r)\b",
    r"\bi\s+(?:heard|got|saw|read)\b",
    r"\bsomeone\s+said\b",
    r"\brumou?r\b",
    r"\bis\s+it\s+true\b",
    r"\bcan\s+you\s+verify\b",
    r"\bverify\s+this\b",
)


class MessyQueryNormalizer:
    def normalize(self, query: str) -> str:
        text = str(query or "")
        for pattern in FILLER_PATTERNS:
            text = re.sub(pattern, " ", text, flags=re.I)
        text = re.sub(r"\b(?:an?|the)\s+news\s+that\b", " ", text, flags=re.I)
        text = re.sub(r"\blatest\s+20\d{2}\b", " ", text, flags=re.I)
        return clean_query_text(text)

    def has_conversational_filler(self, query: str) -> bool:
        text = str(query or "")
        return any(re.search(pattern, text, flags=re.I) for pattern in FILLER_PATTERNS)


def clean_query_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip(" .,!?:;-")).strip()


def normalize_messy_query(query: str) -> str:
    return MessyQueryNormalizer().normalize(query)
