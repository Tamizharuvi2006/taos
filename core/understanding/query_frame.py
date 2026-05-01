from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import List

from taos.core.semantic.query_normalizer import normalize_user_query


_TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_SPANISH_MARKER_RE = re.compile(r"[¿¡]|(?:\bqui[eé]n\b)|(?:\bfund[oó]\b)", re.I)


@dataclass(frozen=True)
class QueryFrame:
    original_query: str
    normalized_query: str
    detected_language: str
    detected_script: str
    canonical_query: str
    intent: str
    lookup_type: str
    entity: str
    role: str
    answer_language: str
    search_queries: List[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "phase148a_query_frame_builder"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class QueryFrameBuilder:
    _FOUNDER_PATTERNS = (
        re.compile(r"^\s*who\s+founded\s+(?P<entity>.+?)\s*$", re.I),
        re.compile(r"^\s*who\s+started\s+(?P<entity>.+?)\s*$", re.I),
        re.compile(r"^\s*who\s+is\s+the\s+founder\s+of\s+(?P<entity>.+?)\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+யாரால்\s+தொடங்கப்பட்டது\??\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+की\s+स्थापना\s+किसने\s+की\??\s*$", re.I),
        re.compile(r"^\s*¿?\s*qui[eé]n\s+fund[oó]\s+(?P<entity>.+?)\??\s*$", re.I),
    )
    _CEO_PATTERNS = (
        re.compile(r"^\s*who\s+is\s+the\s+ceo\s+of\s+(?P<entity>.+?)\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+ceo\s+யார்\??\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+का\s+ceo\s+कौन\s+है\??\s*$", re.I),
        re.compile(r"^\s*¿?\s*qui[eé]n\s+es\s+el\s+ceo\s+de\s+(?P<entity>.+?)\??\s*$", re.I),
    )
    _LINKEDIN_PATTERNS = (
        re.compile(r"^\s*find\s+linkedin\s+(?:of|for)\s+(?P<entity>.+?)\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+linkedin\s+கண்டுபிடி\s*$", re.I),
    )
    _OFFICIAL_WEBSITE_PATTERNS = (
        re.compile(r"^\s*(?P<entity>.+?)\s+official\s+website\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+official\s+site\s*$", re.I),
    )
    _REAL_COMPANY_PATTERNS = (
        re.compile(r"^\s*is\s+(?P<entity>.+?)\s+(?:a\s+)?real\s+company\??\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+உண்மையான\s+company\s+ஆ\??\s*$", re.I),
    )

    def build(self, query: str) -> QueryFrame:
        original_query = str(query or "").strip()
        normalized_query = normalize_user_query(original_query)
        detected_language = self._detect_language(original_query)
        detected_script = self._detect_script(original_query)

        match = self._first_match(normalized_query, self._FOUNDER_PATTERNS)
        if match:
            entity = self._clean_entity(match.group("entity"))
            canonical = f"who founded {entity}"
            return self._frame(
                original_query=original_query,
                normalized_query=normalized_query,
                detected_language=detected_language,
                detected_script=detected_script,
                canonical_query=canonical,
                intent="entity_lookup",
                lookup_type="founder_lookup",
                entity=entity,
                role="founder",
                answer_language=detected_language,
                confidence=0.96 if detected_language == "en" else 0.92,
            )

        match = self._first_match(normalized_query, self._CEO_PATTERNS)
        if match:
            entity = self._clean_entity(match.group("entity"))
            canonical = f"who is the CEO of {entity}"
            return self._frame(
                original_query=original_query,
                normalized_query=normalized_query,
                detected_language=detected_language,
                detected_script=detected_script,
                canonical_query=canonical,
                intent="entity_lookup",
                lookup_type="ceo_lookup",
                entity=entity,
                role="ceo",
                answer_language=detected_language,
                confidence=0.96 if detected_language == "en" else 0.92,
            )

        match = self._first_match(normalized_query, self._LINKEDIN_PATTERNS)
        if match:
            entity = self._clean_entity(match.group("entity"))
            canonical = f"find LinkedIn of {entity}"
            return self._frame(
                original_query=original_query,
                normalized_query=normalized_query,
                detected_language=detected_language,
                detected_script=detected_script,
                canonical_query=canonical,
                intent="entity_lookup",
                lookup_type="linkedin_profile",
                entity=entity,
                role="",
                answer_language=detected_language,
                confidence=0.95 if detected_language == "en" else 0.9,
            )

        match = self._first_match(normalized_query, self._OFFICIAL_WEBSITE_PATTERNS)
        if match:
            entity = self._clean_entity(match.group("entity"))
            canonical = f"{entity} official website"
            return self._frame(
                original_query=original_query,
                normalized_query=normalized_query,
                detected_language=detected_language,
                detected_script=detected_script,
                canonical_query=canonical,
                intent="entity_lookup",
                lookup_type="official_website",
                entity=entity,
                role="",
                answer_language=detected_language,
                confidence=0.94,
            )

        match = self._first_match(normalized_query, self._REAL_COMPANY_PATTERNS)
        if match:
            entity = self._clean_entity(match.group("entity"))
            canonical = f"is {entity} a real company"
            return self._frame(
                original_query=original_query,
                normalized_query=normalized_query,
                detected_language=detected_language,
                detected_script=detected_script,
                canonical_query=canonical,
                intent="entity_lookup",
                lookup_type="business_legitimacy",
                entity=entity,
                role="",
                answer_language=detected_language,
                confidence=0.94 if detected_language == "en" else 0.89,
            )

        return self._frame(
            original_query=original_query,
            normalized_query=normalized_query,
            detected_language=detected_language,
            detected_script=detected_script,
            canonical_query=normalized_query,
            intent="unknown",
            lookup_type="unknown",
            entity="",
            role="",
            answer_language=detected_language,
            confidence=0.0,
        )

    def _frame(
        self,
        *,
        original_query: str,
        normalized_query: str,
        detected_language: str,
        detected_script: str,
        canonical_query: str,
        intent: str,
        lookup_type: str,
        entity: str,
        role: str,
        answer_language: str,
        confidence: float,
    ) -> QueryFrame:
        search_queries = [canonical_query]
        if original_query and original_query != canonical_query:
            search_queries.append(original_query)
        return QueryFrame(
            original_query=original_query,
            normalized_query=normalized_query,
            detected_language=detected_language,
            detected_script=detected_script,
            canonical_query=canonical_query,
            intent=intent,
            lookup_type=lookup_type,
            entity=entity,
            role=role,
            answer_language=answer_language,
            search_queries=search_queries,
            confidence=round(float(confidence), 3),
        )

    @staticmethod
    def _first_match(text: str, patterns: tuple[re.Pattern[str], ...]) -> re.Match[str] | None:
        for pattern in patterns:
            match = pattern.match(text)
            if match:
                return match
        return None

    @staticmethod
    def _clean_entity(value: str) -> str:
        text = re.sub(r"^[\"'`]+|[\"'`?!.]+$", "", str(value or "").strip())
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _detect_language(query: str) -> str:
        text = str(query or "")
        if _TAMIL_RE.search(text):
            return "ta"
        if _DEVANAGARI_RE.search(text):
            return "hi"
        if _SPANISH_MARKER_RE.search(text):
            return "es"
        return "en"

    @staticmethod
    def _detect_script(query: str) -> str:
        text = str(query or "")
        if _TAMIL_RE.search(text):
            return "Tamil"
        if _DEVANAGARI_RE.search(text):
            return "Devanagari"
        return "Latin"
