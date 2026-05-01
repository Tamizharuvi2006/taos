from __future__ import annotations

import re
from difflib import SequenceMatcher


class HandleMatcher:
    def score(self, entity_name: str, handle: str) -> float:
        entity = re.sub(r"[^a-z0-9]", "", str(entity_name or "").lower())
        clean_handle = re.sub(r"[^a-z0-9]", "", str(handle or "").lower().lstrip("@"))
        if not entity or not clean_handle:
            return 0.0
        if entity == clean_handle:
            return 1.0
        if entity in clean_handle or clean_handle in entity:
            return 0.86
        return round(SequenceMatcher(None, entity, clean_handle).ratio(), 3)
