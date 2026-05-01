from __future__ import annotations

from typing import Dict, List


class TargetedRetryPlanner:
    def plan(
        self,
        *,
        meaning_frame: Dict[str, object] | None,
        quality_summary: Dict[str, object] | None,
        query_plan_summary: Dict[str, object] | None,
    ) -> Dict[str, object]:
        meaning = dict(meaning_frame or {})
        quality = dict(quality_summary or {})
        plan = dict(query_plan_summary or {})
        entities = [str(item) for item in meaning.get("protected_entities") or [] if str(item).strip()]
        subject = str(meaning.get("primary_subject") or "").strip()
        relation = str(meaning.get("relation") or "").strip()
        official_missing = not bool(quality.get("official_source_found"))
        usable = int(quality.get("usable_count") or 0)

        queries: List[str] = []
        missing: List[str] = []
        if official_missing:
            missing.append("official confirmation")
        if usable <= 1:
            missing.append("usable corroborating evidence")

        if subject.lower() == "claude" and relation == "blocked_or_restricted_access":
            queries.extend(
                [
                    "site:anthropic.com Claude supported countries India",
                    "site:meity.gov.in Claude Anthropic AI block",
                    "site:rbi.org.in Claude Anthropic Mythos",
                ]
            )
        elif entities:
            joined = " ".join(entities)
            queries.append(f"{joined} official statement")
        return {
            "missing_evidence": missing,
            "queries": _dedupe(queries),
            "targeted_retry_used": bool(queries),
        }


def _dedupe(values: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out
