from __future__ import annotations

import re
from typing import Dict


class LanguageHintDetector:
    TAMIL_ENGLISH_HINTS = {"agala", "illa", "iruka", "ah", "da", "work agala"}

    def detect(self, query: str) -> Dict[str, object]:
        text = str(query or "").lower()
        hints = [hint for hint in self.TAMIL_ENGLISH_HINTS if hint in text]
        return {
            "primary": "mixed_english" if hints else "english",
            "mixed": bool(hints),
            "hints": hints,
            "ascii_ratio": _ascii_ratio(text),
        }


def _ascii_ratio(text: str) -> float:
    if not text:
        return 1.0
    return sum(1 for ch in text if ord(ch) < 128) / len(text)
