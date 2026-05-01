from __future__ import annotations

from dataclasses import dataclass
import re

from taos.core.understanding.query_language import LanguageProfile


@dataclass(frozen=True)
class FastPathMatch:
    canonical_query: str
    intent: str
    lookup_type: str
    entity: str
    role: str
    confidence: float
    source: str = "rule_based_fast_path"


class RuleBasedFastPathCanonicalizer:
    """Deterministic seed rules only for obvious supported lookup shapes."""

    _FOUNDER_PATTERNS = (
        re.compile(r"^\s*who\s+(?:founded|started)\s+(?P<entity>.+?)\s*$", re.I),
        re.compile(r"^\s*who\s+is\s+the\s+founder\s+of\s+(?P<entity>.+?)\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+\u0baf\u0bbe\u0bb0\u0bbe\u0bb2\u0bcd\s+\u0ba4\u0bca\u0b9f\u0b99\u0bcd\u0b95\u0baa\u0bcd\u0baa\u0b9f\u0bcd\u0b9f\u0ba4\u0bc1\??\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+\u0915\u0940\s+\u0938\u094d\u0925\u093e\u092a\u0928\u093e\s+\u0915\u093f\u0938\u0928\u0947\s+\u0915\u0940\??\s*$", re.I),
        re.compile(r"^\s*¿?\s*qui[eé]n\s+fund[oó]\s+(?P<entity>.+?)\??\s*$", re.I),
        re.compile(r"^\s*qui\s+a\s+fond[ée]\s+(?P<entity>.+?)\s*\??\s*$", re.I),
    )
    _CEO_PATTERNS = (
        re.compile(r"^\s*who\s+is\s+the\s+ceo\s+of\s+(?P<entity>.+?)\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+ceo\s+\u0baf\u0bbe\u0bb0\u0bcd\??\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+\u0915\u093e\s+ceo\s+\u0915\u094c\u0928\s+\u0939\u0948\??\s*$", re.I),
        re.compile(r"^\s*¿?\s*qui[eé]n\s+es\s+el\s+ceo\s+de\s+(?P<entity>.+?)\??\s*$", re.I),
        re.compile(r"^\s*qui\s+est\s+le\s+pdg\s+de\s+(?P<entity>.+?)\s*\??\s*$", re.I),
    )
    _LINKEDIN_PATTERNS = (
        re.compile(r"^\s*find\s+linkedin\s+(?:of|for)\s+(?P<entity>.+?)\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+linkedin\s+\u0b95\u0ba3\u0bcd\u0b9f\u0bc1\u0baa\u0bbf\u0b9f\u0bbf\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+\u0915\u093e\s+linkedin\s+\u0922\u0942\u0902\u0922\u094b\s*$", re.I),
        re.compile(r"^\s*encuentra\s+el\s+linkedin\s+de\s+(?P<entity>.+?)\s*$", re.I),
        re.compile(r"^\s*trouve\s+le\s+linkedin\s+de\s+(?P<entity>.+?)\s*$", re.I),
    )
    _OFFICIAL_WEBSITE_PATTERNS = (
        re.compile(r"^\s*(?P<entity>.+?)\s+official\s+(?:website|site)\s*$", re.I),
    )
    _REAL_COMPANY_PATTERNS = (
        re.compile(r"^\s*is\s+(?P<entity>.+?)\s+(?:a\s+)?real\s+company\??\s*$", re.I),
        re.compile(r"^\s*¿?\s*(?P<entity>.+?)\s+es\s+una\s+empresa\s+real\??\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+est-?elle\s+une\s+vraie\s+entreprise\??\s*$", re.I),
        re.compile(r"^\s*(?P<entity>.+?)\s+\u0b89\u0ba3\u0bcd\u0bae\u0bc8\u0baf\u0bbe\u0ba9\s+company\s+\u0b86\??\s*$", re.I),
        re.compile(r"^\s*\u0915\u094d\u092f\u093e\s+(?P<entity>.+?)\s+\u0905\u0938\u0932\u0940\s+\u0915\u0902\u092a\u0928\u0940\s+\u0939\u0948\??\s*$", re.I),
    )

    def canonicalize(self, *, normalized_query: str, language_profile: LanguageProfile) -> FastPathMatch | None:
        for pattern in self._FOUNDER_PATTERNS:
            match = pattern.match(normalized_query)
            if match:
                entity = self._clean_entity(match.group("entity"))
                return FastPathMatch(
                    canonical_query=f"who founded {entity}",
                    intent="entity_lookup",
                    lookup_type="founder_lookup",
                    entity=entity,
                    role="founder",
                    confidence=0.96 if language_profile.detected_language == "en" else 0.92,
                )
        for pattern in self._CEO_PATTERNS:
            match = pattern.match(normalized_query)
            if match:
                entity = self._clean_entity(match.group("entity"))
                return FastPathMatch(
                    canonical_query=f"who is the CEO of {entity}",
                    intent="entity_lookup",
                    lookup_type="ceo_lookup",
                    entity=entity,
                    role="ceo",
                    confidence=0.96 if language_profile.detected_language == "en" else 0.92,
                )
        for pattern in self._LINKEDIN_PATTERNS:
            match = pattern.match(normalized_query)
            if match:
                entity = self._clean_entity(match.group("entity"))
                return FastPathMatch(
                    canonical_query=f"find LinkedIn of {entity}",
                    intent="entity_lookup",
                    lookup_type="linkedin_profile",
                    entity=entity,
                    role="",
                    confidence=0.9,
                )
        for pattern in self._OFFICIAL_WEBSITE_PATTERNS:
            match = pattern.match(normalized_query)
            if match:
                entity = self._clean_entity(match.group("entity"))
                return FastPathMatch(
                    canonical_query=f"{entity} official website",
                    intent="entity_lookup",
                    lookup_type="official_website",
                    entity=entity,
                    role="",
                    confidence=0.9,
                )
        for pattern in self._REAL_COMPANY_PATTERNS:
            match = pattern.match(normalized_query)
            if match:
                entity = self._clean_entity(match.group("entity"))
                return FastPathMatch(
                    canonical_query=f"is {entity} a real company",
                    intent="entity_lookup",
                    lookup_type="business_legitimacy",
                    entity=entity,
                    role="",
                    confidence=0.89,
                )
        return None

    @staticmethod
    def _clean_entity(value: str) -> str:
        text = re.sub(r"^[\"'`]+|[\"'`?!.]+$", "", str(value or "").strip())
        text = re.sub(r"\s+", " ", text)
        return text.strip()
