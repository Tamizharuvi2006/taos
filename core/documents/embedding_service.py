"""Deterministic lightweight embedding service for retrieval."""

from __future__ import annotations

import hashlib
import math
from typing import List

from taos.core.documents.utils import tokenize


class EmbeddingService:
    def __init__(self, dimensions: int = 96) -> None:
        self._dimensions = max(32, int(dimensions))

    def embed_text(self, text: str) -> List[float]:
        vector = [0.0] * self._dimensions
        for token in tokenize(text):
            idx = self._hash_token(token) % self._dimensions
            vector[idx] += 1.0
        return self._normalize(vector)

    @staticmethod
    def _hash_token(token: str) -> int:
        h = hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
        return int(h, 16)

    @staticmethod
    def _normalize(vector: List[float]) -> List[float]:
        norm = math.sqrt(sum(v * v for v in vector))
        if norm <= 0:
            return vector
        return [round(v / norm, 6) for v in vector]

