from __future__ import annotations

from .intent_frame import IntentFrame, RelationFrame


class SemanticQueryRewriter:
    def normalized_question(self, *, country: str = "", product: str = "", company: str = "", relation: RelationFrame | None = None) -> str:
        obj = product or company
        if not obj:
            return ""
        rel = (relation.relation if relation else "") or ""
        if rel == "blocked_or_restricted_access":
            if country:
                return f"Is {obj} blocked or restricted in {country}?"
            return f"Is {obj} blocked or restricted?"
        if rel == "unavailable_or_outage":
            if country:
                return f"Is {obj} unavailable or having an outage in {country}?"
            return f"Is {obj} unavailable or having an outage?"
        if rel == "released_or_changed":
            return f"What changed or was released for {obj}?"
        if rel == "acquired_or_merged":
            return f"Was {obj} acquired or merged?"
        if rel == "pricing_changed":
            return f"Did pricing change for {obj}?"
        if rel == "security_concern":
            return f"What security concern is reported about {obj}?"
        return f"What is the current status of {obj}?"

    def low_confidence_prompt(self, frame: IntentFrame) -> str:
        return "\n".join(
            [
                "Rewrite this messy user request into one clean research claim/question.",
                "Preserve uncertainty. Do not invent facts. Keep entities and dates if present.",
                "Return only the corrected research query.",
                "",
                f"Original: {frame.original_query}",
                f"Cleaned: {frame.cleaned_query}",
                f"Detected intent: {frame.intent}",
                f"Entities: {frame.entities}",
                f"Relation: {frame.relation or 'unknown'}",
            ]
        ).strip()
