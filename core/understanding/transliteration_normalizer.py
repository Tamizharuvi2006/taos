from __future__ import annotations

import re


class TransliterationNormalizer:
    PHRASES = {
        "work agala": "not working",
        "agala": "not working",
        "illa": "not available",
        "iruka": "available",
        "ah": "",
    }

    def normalize(self, query: str) -> str:
        text = str(query or "")
        for phrase, replacement in self.PHRASES.items():
            text = re.sub(rf"\b{re.escape(phrase)}\b", replacement, text, flags=re.I)
        return re.sub(r"\s+", " ", text).strip()
