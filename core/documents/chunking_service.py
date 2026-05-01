"""Chunking logic for document text."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from taos.core.documents.utils import normalize_whitespace


class ChunkingService:
    def __init__(self, chunk_words: int = 420, overlap_words: int = 70) -> None:
        self._chunk_words = max(120, int(chunk_words))
        self._overlap_words = max(20, min(int(overlap_words), self._chunk_words // 2))

    def chunk_pages(self, page_texts: List[str]) -> List[Dict[str, Optional[int] | int | str]]:
        tokens = self._flatten_page_tokens(page_texts or [])
        if not tokens:
            return []

        chunks: List[Dict[str, Optional[int] | int | str]] = []
        start = 0
        chunk_index = 0
        total = len(tokens)
        while start < total:
            end = min(start + self._chunk_words, total)
            window = tokens[start:end]
            text = normalize_whitespace(" ".join(tok for tok, _ in window))
            if text:
                pages = [p for _, p in window if p is not None]
                chunks.append(
                    {
                        "chunk_index": chunk_index,
                        "chunk_text": text,
                        "page_start": min(pages) if pages else None,
                        "page_end": max(pages) if pages else None,
                        "token_count": len(window),
                    }
                )
                chunk_index += 1
            if end >= total:
                break
            start = max(0, end - self._overlap_words)
        return chunks

    def chunk_text(self, text: str) -> List[Dict[str, Optional[int] | int | str]]:
        words = (normalize_whitespace(text) or "").split(" ")
        if not words or words == [""]:
            return []
        pseudo_pages = [" ".join(words)]
        return self.chunk_pages(pseudo_pages)

    def _flatten_page_tokens(self, page_texts: List[str]) -> List[Tuple[str, Optional[int]]]:
        out: List[Tuple[str, Optional[int]]] = []
        for i, page in enumerate(page_texts, start=1):
            clean = normalize_whitespace(page)
            if not clean:
                continue
            out.extend((tok, i) for tok in clean.split(" ") if tok)
        return out

