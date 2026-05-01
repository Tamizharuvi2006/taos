from __future__ import annotations

from typing import Any, Dict, List


class ExtractRecovery:
    def recover(
        self,
        *,
        ranked_rows: List[Dict[str, Any]],
        official_source_required: bool = False,
    ) -> Dict[str, Any]:
        recovered_rows: List[Dict[str, Any]] = []
        for row in ranked_rows:
            snippet = str(row.get("snippet") or "").strip()
            if not snippet:
                continue
            recovered = dict(row)
            recovered["snippet_only"] = True
            recovered["extract_quality"] = "snippet_only"
            recovered["extract_quality_score"] = max(0.2, float(row.get("extract_quality_score") or 0.25))
            recovered["limited_verification"] = True
            recovered_rows.append(recovered)
        return {
            "used": bool(recovered_rows),
            "warning": (
                "Extraction failed, so this answer is grounded in search snippets only."
                if recovered_rows
                else "Extraction failed and snippet fallback was unavailable."
            ),
            "official_source_required": bool(official_source_required),
            "recovered_rows": recovered_rows,
        }
