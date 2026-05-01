"""Utilities for document processing and retrieval."""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Iterable, List, Sequence

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "with",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z0-9_]+", (text or "").lower())


def keyword_tokens(text: str, limit: int = 32) -> List[str]:
    out: List[str] = []
    seen = set()
    for tok in tokenize(text):
        if len(tok) <= 2 or tok in _STOPWORDS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    if not vec_a or not vec_b:
        return 0.0
    size = min(len(vec_a), len(vec_b))
    if size == 0:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for i in range(size):
        a = float(vec_a[i])
        b = float(vec_b[i])
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return max(0.0, min(1.0, dot / (math.sqrt(norm_a) * math.sqrt(norm_b))))


def question_overlap_ratio(question_terms: Iterable[str], chunk_terms: Iterable[str]) -> float:
    q = {t for t in question_terms if t}
    c = {t for t in chunk_terms if t}
    if not q or not c:
        return 0.0
    return len(q.intersection(c)) / float(len(q))


def build_cache_key(
    user_id: str,
    doc_ids: Sequence[str],
    question: str,
    model: str,
    mode: str = "",
    mark_format: str = "",
    output_format: str = "",
) -> str:
    normalized = normalize_whitespace(question).lower()
    payload = "|".join(
        [
            user_id,
            ",".join(sorted(doc_ids)),
            normalized,
            normalize_whitespace(str(mode or "")).lower(),
            normalize_whitespace(str(mark_format or "")).lower(),
            normalize_whitespace(str(output_format or "")).lower(),
            model,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
