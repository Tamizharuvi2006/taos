from __future__ import annotations

from typing import Dict, Iterable, List

from .entity_resolver import fuzzy_score
from .intent_frame import CorrectionCandidate, RelationFrame


RELATION_CATALOG: Dict[str, Dict[str, object]] = {
    "block": {"canonical": "blocked_or_restricted_access", "variants": ("blocked", "blocking", "restricted")},
    "blocked": {"canonical": "blocked_or_restricted_access", "variants": ("blocked", "blocking", "restricted")},
    "blocking": {"canonical": "blocked_or_restricted_access", "variants": ("blocked", "blocking", "restricted")},
    "lock": {"canonical": "blocked_or_restricted_access", "variants": ("blocked", "blocking", "locking", "restricted")},
    "locked": {"canonical": "blocked_or_restricted_access", "variants": ("blocked", "blocking", "locking", "restricted")},
    "locking": {"canonical": "blocked_or_restricted_access", "variants": ("blocked", "blocking", "locking", "restricted")},
    "ban": {"canonical": "blocked_or_restricted_access", "variants": ("ban", "banned", "banning")},
    "banned": {"canonical": "blocked_or_restricted_access", "variants": ("ban", "banned", "banning")},
    "banning": {"canonical": "blocked_or_restricted_access", "variants": ("ban", "banned", "banning")},
    "restrict": {"canonical": "blocked_or_restricted_access", "variants": ("restricted", "restricting")},
    "restricted": {"canonical": "blocked_or_restricted_access", "variants": ("restricted", "restricting")},
    "restricting": {"canonical": "blocked_or_restricted_access", "variants": ("restricted", "restricting")},
    "unavailable": {"canonical": "unavailable_or_outage", "variants": ("unavailable", "not available")},
    "outage": {"canonical": "unavailable_or_outage", "variants": ("outage", "down", "service issue")},
    "down": {"canonical": "unavailable_or_outage", "variants": ("outage", "down", "service issue")},
    "released": {"canonical": "released_or_changed", "variants": ("released", "launched", "release")},
    "release": {"canonical": "released_or_changed", "variants": ("released", "launched", "release")},
    "changed": {"canonical": "released_or_changed", "variants": ("changed", "update", "recent changes")},
    "acquired": {"canonical": "acquired_or_merged", "variants": ("acquired", "acquisition", "merged")},
    "merged": {"canonical": "acquired_or_merged", "variants": ("merged", "acquisition")},
    "pricing": {"canonical": "pricing_changed", "variants": ("pricing changed", "pricing update")},
    "security": {"canonical": "security_concern", "variants": ("security concern", "cybersecurity concern")},
    "cybersecurity": {"canonical": "security_concern", "variants": ("security concern", "cybersecurity concern")},
}


class RelationInferencer:
    def infer(self, tokens: Iterable[str], correction_candidates: Iterable[CorrectionCandidate] = ()) -> RelationFrame:
        semantic_tokens = [str(token or "").lower() for token in tokens if str(token or "").strip()]
        semantic_tokens.extend(candidate.candidate for candidate in correction_candidates if candidate.kind == "relation")
        best_key = ""
        best_score = 0.0
        for token in semantic_tokens:
            for key in RELATION_CATALOG:
                score = 1.0 if token == key else fuzzy_score(token, key)
                threshold = 0.61
                if score >= threshold and score > best_score:
                    best_key = key
                    best_score = score
        if not best_key:
            if "not" in semantic_tokens and "available" in semantic_tokens:
                best_key = "unavailable"
                best_score = 0.82
            elif "not" in semantic_tokens and "working" in semantic_tokens:
                best_key = "outage"
                best_score = 0.78
            else:
                return RelationFrame(relation="", canonical="", variants=(), score=0.0)
        meta = RELATION_CATALOG[best_key]
        variants = tuple(_dedupe(str(item) for item in meta.get("variants") or ()))
        return RelationFrame(
            relation=str(meta.get("canonical") or ""),
            canonical=best_key,
            variants=variants,
            score=round(best_score, 3),
        )

    def correction_candidates(self, tokens: Iterable[str]) -> tuple[CorrectionCandidate, ...]:
        candidates: List[CorrectionCandidate] = []
        for token in tokens:
            raw = str(token or "").lower()
            if len(raw) < 3:
                continue
            for key in RELATION_CATALOG:
                score = 1.0 if raw == key else fuzzy_score(raw, key)
                if raw == key or score >= 0.61:
                    candidates.append(
                        CorrectionCandidate(
                            token=raw,
                            candidate=key,
                            kind="relation",
                            score=round(score, 3),
                        )
                    )
        candidates.sort(key=lambda item: item.score, reverse=True)
        return tuple(_dedupe_candidates(candidates))[:10]


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(text)
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
