"""PDF extraction service."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import List

from taos.core.documents.utils import normalize_whitespace


@dataclass
class PdfExtractionResult:
    text: str
    page_texts: List[str]
    page_count: int


class PdfService:
    def extract_pdf(self, pdf_bytes: bytes) -> PdfExtractionResult:
        if not pdf_bytes:
            raise ValueError("Empty PDF payload")

        reader = self._make_reader(pdf_bytes)
        page_texts: List[str] = []
        for page in reader.pages:
            raw = page.extract_text() or ""
            clean = normalize_whitespace(raw)
            page_texts.append(clean)

        combined = normalize_whitespace(" ".join(p for p in page_texts if p))
        return PdfExtractionResult(
            text=combined,
            page_texts=page_texts,
            page_count=len(page_texts),
        )

    def _make_reader(self, pdf_bytes: bytes):
        try:
            from pypdf import PdfReader

            return PdfReader(BytesIO(pdf_bytes))
        except Exception:
            pass

        try:
            from PyPDF2 import PdfReader

            return PdfReader(BytesIO(pdf_bytes))
        except Exception as exc:
            raise RuntimeError(
                "No PDF parser available. Install pypdf (recommended) or PyPDF2."
            ) from exc

