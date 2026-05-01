from __future__ import annotations

from typing import Any, Dict, List

from .research_pipeline import build_rumour_no_confirmation_answer, normalize_rumour_claim_query


class NoResultHandler:
    def build(
        self,
        *,
        goal: str,
        checked_queries: List[str],
        official_checked: bool = True,
        recent_checked: bool = True,
        trusted_checked: bool = True,
    ) -> Dict[str, Any]:
        claim = normalize_rumour_claim_query(goal)
        if claim.detected:
            answer = build_rumour_no_confirmation_answer(
                query=goal,
                checked_queries=checked_queries or list(claim.queries),
            )
            return {
                "answer": answer,
                "warnings": ["Exact rumour not confirmed."],
                "confidence": 0.38,
                "suggestions": [
                    "Check the official supported-countries page.",
                    "Separate outage/account stories from country-wide access blocks.",
                    "Retry with official sources only if the claim is high impact.",
                ],
                "metadata": {
                    "no_result": True,
                    "rumour_claim": True,
                    "goal": str(goal or "").strip(),
                    "normalized_claim": claim.normalized_query,
                },
            }
        suggestions = [
            "Search with the exact company, person, or product name.",
            "Add a location or exact date range.",
            "Ask for broader background instead of the exact latest claim.",
        ]
        checked = []
        if official_checked:
            checked.append("official sources")
        if recent_checked:
            checked.append("recent search results")
        if trusted_checked:
            checked.append("related trusted sources")
        lines = [
            "I couldn't verify this confidently from reliable sources.",
            "",
            "What was searched:",
        ]
        if checked_queries:
            for query in checked_queries[:4]:
                lines.append(f"- {query}")
        else:
            lines.append("- The requested topic and related source variants.")
        lines.extend([
            "",
            "What I checked:",
        ])
        for item in checked:
            lines.append(f"- {item}")
        if checked_queries:
            lines.append("")
            lines.append("Queries tried:")
            for query in checked_queries[:4]:
                lines.append(f"- {query}")
        lines.extend(
            [
                "",
                "What was not found:",
                "- A reliable source-grounded confirmation for the exact claim.",
                "",
                "Sources",
                "[S1] Search attempt log - no reliable matching result returned.",
            ]
        )
        lines.extend(
            [
                "",
                "Possible reasons:",
                "- Topic is too new.",
                "- Entity name may be misspelled or ambiguous.",
                "- Reliable sources are unavailable.",
                "- The query is too broad.",
                "- None passed ranking/diversity quality checks.",
                "",
                "Next useful moves:",
            ]
        )
        for idx, suggestion in enumerate(suggestions, start=1):
            lines.append(f"{idx}. {suggestion}")
        return {
            "answer": "\n".join(lines).strip(),
            "warnings": ["No reliable evidence found."],
            "confidence": 0.12,
            "suggestions": suggestions,
            "metadata": {
                "no_result": True,
                "goal": str(goal or "").strip(),
            },
        }
