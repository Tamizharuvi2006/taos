from __future__ import annotations

import re
from typing import Dict

from taos.core.semantic.query_normalizer import normalize_user_query


TRAILING_FILLER = {
    "company",
    "startup",
    "business",
    "brand",
    "person",
    "profile",
    "page",
    "official",
    "instagram",
    "insta",
    "linkedin",
    "details",
    "is",
    "this",
    "that",
    "it",
    "local",
}


class PublicEntityResolver:
    def resolve(self, query: str) -> Dict[str, str]:
        text = str(query or "").strip()
        lowered = text.lower()
        entity = self._extract_entity(lowered)
        return {
            "entity_name": _title_entity(entity),
            "entity_key": entity,
            "entity_type": self._entity_type(entity, lowered),
        }

    def _extract_entity(self, lowered: str) -> str:
        normalized = _strip_query_prefixes(lowered)
        patterns = (
            r"\bwho\s+(?:founded|started)\s+(?P<entity>[a-z0-9&.\- ]+)",
            r"\bfind\s+linkedin\s+(?:of|for)\s+(?P<entity>[a-z0-9&.\- ]+)",
            r"\b(?P<entity>[a-z0-9&.\- ]+?)\s+official\s+website\b",
            r"\b(?:find|show|get)\s+(?P<entity>[a-z0-9&.\- ]+?)\s+linkedin\b",
            r"\b(?P<entity>[a-z0-9&.\- ]+?)\s+(?:ceo|founder|owner)\b",
            r"\b(?:ceo|founder|founded|owner)\s+(?:(?:of|fo|for)\s+)?(?P<entity>[a-z0-9&.\- ]+)",
            r"\b(?:of|fo|for)\s+(?P<entity>[a-z0-9&.\- ]+)",
            r"\b(?:is|verify)\s+(?P<entity>[a-z0-9&.\- ]+?)\s+(?:real|legit|registered|company|startup)\b",
            r"\b(?:what\s+does|what\s+is)\s+(?P<entity>[a-z0-9&.\- ]+?)\s+(?:do|company|startup)?\b",
            r"\b(?P<entity>[a-z0-9&.\- ]+?)\s+(?:real|legit|registered)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized, re.I)
            if not match:
                continue
            value = _clean_entity(match.group("entity"))
            if value and value not in {"this", "that", "it", "local", "is this"}:
                return value
        return ""

    def _entity_type(self, entity: str, query: str) -> str:
        if not entity:
            return "unknown"
        if any(marker in query for marker in ("company", "startup", "infotech", "labs", "technologies", "pvt", "llc", "inc")):
            return "company"
        return "company"


def _clean_entity(value: str) -> str:
    text = re.sub(
        r"\b(?:official|website|instagram|insta|linkedin|profile|page|ceo|founder|founded|owner|details|real|legit|registered|company|startup|who|find|show|get)\b",
        " ",
        str(value or ""),
        flags=re.I,
    )
    parts = [part for part in re.sub(r"[^a-z0-9&.\- ]", " ", text.lower()).split() if part not in TRAILING_FILLER]
    if not parts or set(parts) <= {"this", "that", "it", "local", "is"}:
        return ""
    return " ".join(parts).strip()


def _strip_query_prefixes(value: str) -> str:
    text = normalize_user_query(value).lower()
    text = re.sub(r"^\s*who\s+is\s+", "", text, flags=re.I)
    text = re.sub(r"^\s*who\s+was\s+", "", text, flags=re.I)
    text = re.sub(r"^\s*who\s+founded\s+", "founded ", text, flags=re.I)
    text = re.sub(r"^\s*find\s+linkedin\s+of\s+", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _title_entity(value: str) -> str:
    small = {"and", "of", "the"}
    words = []
    for word in str(value or "").split():
        if word in small:
            words.append(word)
        elif word in {"llc", "inc", "pvt"}:
            words.append(word.upper())
        else:
            words.append(word.capitalize())
    return " ".join(words)
