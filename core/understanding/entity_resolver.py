from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Dict, Iterable, List

from .intent_frame import CorrectionCandidate, EntityMention


ENTITY_CATALOG: Dict[str, Dict[str, str]] = {
    "anthropic": {"display": "Anthropic", "kind": "company", "official_domain": "anthropic.com"},
    "angular": {"display": "Angular", "kind": "tool_framework", "company": "Google", "official_domain": "angular.dev"},
    "claude": {"display": "Claude", "kind": "product", "company": "Anthropic", "official_domain": "anthropic.com"},
    "deepseek": {"display": "DeepSeek", "kind": "product", "company": "DeepSeek", "official_domain": "deepseek.com"},
    "firebase": {"display": "Firebase", "kind": "product", "company": "Google", "official_domain": "firebase.google.com"},
    "gemini": {"display": "Gemini", "kind": "product", "company": "Google", "official_domain": "gemini.google.com"},
    "github": {"display": "GitHub", "kind": "tool_framework", "official_domain": "github.com"},
    "google": {"display": "Google", "kind": "company", "official_domain": "google.com"},
    "grok": {"display": "Grok", "kind": "product", "company": "xAI", "official_domain": "x.ai"},
    "india": {"display": "India", "kind": "country"},
    "europe": {"display": "Europe", "kind": "country"},
    "microsoft": {"display": "Microsoft", "kind": "company", "official_domain": "microsoft.com"},
    "openai": {"display": "OpenAI", "kind": "company", "official_domain": "openai.com"},
    "chatgpt": {"display": "ChatGPT", "kind": "product", "company": "OpenAI", "official_domain": "openai.com"},
    "react": {"display": "React", "kind": "tool_framework", "company": "Meta", "official_domain": "react.dev"},
    "vite": {"display": "Vite", "kind": "tool_framework", "official_domain": "vitejs.dev"},
}

ENTITY_ALIASES: Dict[str, str] = {
    "claud": "claude",
    "claudde": "claude",
    "cluade": "claude",
    "anthoropic": "anthropic",
    "chatgptt": "chatgpt",
    "deep seek": "deepseek",
    "gemeni": "gemini",
    "gpt": "chatgpt",
}

GENERIC_NON_ENTITY_TOKENS = {
    "cloud",
    "clouds",
    "clouding",
    "compute",
    "computing",
}


class EntityResolver:
    def resolve(self, tokens: Iterable[str]) -> tuple[EntityMention, ...]:
        mentions: List[EntityMention] = []
        for token in tokens:
            match = self.best_match(str(token or ""))
            if not match:
                continue
            mentions.append(match)
        return tuple(_dedupe_mentions(mentions))

    def correction_candidates(self, tokens: Iterable[str]) -> tuple[CorrectionCandidate, ...]:
        candidates: List[CorrectionCandidate] = []
        for token in tokens:
            raw = str(token or "").lower()
            if len(raw) < 3:
                continue
            if raw in GENERIC_NON_ENTITY_TOKENS:
                continue
            for key, meta in ENTITY_CATALOG.items():
                score = fuzzy_score(raw, key)
                if raw == key or score >= 0.68:
                    candidates.append(
                        CorrectionCandidate(
                            token=raw,
                            candidate=key,
                            kind=str(meta.get("kind") or "entity"),
                            score=round(1.0 if raw == key else score, 3),
                        )
                    )
        candidates.sort(key=lambda item: item.score, reverse=True)
        return tuple(_dedupe_candidates(candidates))[:10]

    def best_match(self, token: str) -> EntityMention | None:
        raw = str(token or "").lower()
        if not raw:
            return None
        if raw in GENERIC_NON_ENTITY_TOKENS:
            return None
        alias_key = ENTITY_ALIASES.get(raw)
        if alias_key and alias_key in ENTITY_CATALOG:
            meta = ENTITY_CATALOG[alias_key]
            return EntityMention(
                text=raw,
                canonical=str(meta.get("display") or alias_key.title()),
                kind=str(meta.get("kind") or "entity"),
                score=0.99,
                company=str(meta.get("company") or ""),
                official_domain=str(meta.get("official_domain") or ""),
            )
        best_key = ""
        best_score = 0.0
        for key in ENTITY_CATALOG:
            score = 1.0 if raw == key else fuzzy_score(raw, key)
            if score > best_score:
                best_key = key
                best_score = score
        if best_score < 0.68:
            return None
        meta = ENTITY_CATALOG[best_key]
        return EntityMention(
            text=raw,
            canonical=str(meta.get("display") or best_key.title()),
            kind=str(meta.get("kind") or "entity"),
            score=round(best_score, 3),
            company=str(meta.get("company") or ""),
            official_domain=str(meta.get("official_domain") or ""),
        )


def fuzzy_score(token: str, candidate: str) -> float:
    seq = SequenceMatcher(None, token, candidate).ratio()
    ngram = _ngram_similarity(token, candidate)
    phonetic = 1.0 if soundex(token) == soundex(candidate) else 0.0
    prefix_bonus = 0.05 if token[:1] == candidate[:1] else 0.0
    return min(1.0, seq * 0.62 + ngram * 0.28 + phonetic * 0.10 + prefix_bonus)


def soundex(value: str) -> str:
    text = re.sub(r"[^a-z]", "", str(value or "").lower())
    if not text:
        return ""
    groups = {
        "bfpv": "1",
        "cgjkqsxz": "2",
        "dt": "3",
        "l": "4",
        "mn": "5",
        "r": "6",
    }
    mapping = {char: code for chars, code in groups.items() for char in chars}
    first = text[0].upper()
    previous = mapping.get(text[0], "")
    encoded: List[str] = []
    for char in text[1:]:
        code = mapping.get(char, "")
        if code and code != previous:
            encoded.append(code)
        previous = code
    return (first + "".join(encoded) + "000")[:4]


def _ngram_similarity(left: str, right: str, n: int = 2) -> float:
    def grams(value: str) -> set[str]:
        if len(value) <= n:
            return {value}
        return {value[idx : idx + n] for idx in range(len(value) - n + 1)}

    a = grams(left)
    b = grams(right)
    return len(a & b) / max(1, len(a | b))


def _dedupe_mentions(mentions: Iterable[EntityMention]) -> List[EntityMention]:
    out: List[EntityMention] = []
    seen = set()
    for mention in mentions:
        key = (mention.kind, mention.canonical)
        if key in seen:
            continue
        seen.add(key)
        out.append(mention)
    return out


def _dedupe_candidates(candidates: Iterable[CorrectionCandidate]) -> List[CorrectionCandidate]:
    out: List[CorrectionCandidate] = []
    seen = set()
    for candidate in candidates:
        key = (candidate.token, candidate.candidate, candidate.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out
