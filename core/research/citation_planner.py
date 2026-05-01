from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


FACTUAL_RE = re.compile(
    r"\b(\d+(?:\.\d+)?%?|\d{4}|latest|current|version|policy|law|regulation|CEO|founder|"
    r"announced|reported|launched|released|approved|banned|requires?)\b",
    re.I,
)


class CitationPlanner:
    """Maps answer paragraphs to source ids and flags unsupported factual text."""

    def plan(self, *, answer: str, source_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        rows = [dict(row) for row in source_rows or []]
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n|(?<=\.)\s+(?=[A-Z])", str(answer or "")) if p.strip()]
        plan: List[Dict[str, Any]] = []
        unsupported: List[str] = []
        for index, paragraph in enumerate(paragraphs, start=1):
            factual = bool(FACTUAL_RE.search(paragraph))
            support = self._supporting_source_ids(paragraph, rows)
            needs_citation = factual or bool(re.search(r"\[S\d+\]", paragraph))
            if needs_citation and not support:
                unsupported.append(paragraph)
            plan.append(
                {
                    "paragraph_index": index,
                    "needs_citation": needs_citation,
                    "source_ids": support,
                    "unsupported": bool(needs_citation and not support),
                }
            )
        return {
            "plan": plan,
            "summary": {
                "paragraph_count": len(paragraphs),
                "citation_required_count": sum(1 for row in plan if row["needs_citation"]),
                "unsupported_factual_paragraphs": len(unsupported),
                "unsupported_examples": unsupported[:3],
            },
        }

    def soften_unsupported(self, *, answer: str, citation_plan: Dict[str, Any]) -> str:
        unsupported = {
            str(row).strip()
            for row in dict(citation_plan.get("summary") or {}).get("unsupported_examples", [])
            if str(row).strip()
        }
        if not unsupported:
            return str(answer or "").strip()
        output = str(answer or "").strip()
        for paragraph in unsupported:
            softened = (
                "The available evidence does not fully verify this point, so treat it as tentative: "
                + paragraph
            )
            output = output.replace(paragraph, softened)
        return output

    def _supporting_source_ids(self, paragraph: str, rows: List[Dict[str, Any]]) -> List[str]:
        explicit = [f"S{match}" for match in re.findall(r"\[S(\d+)\]", paragraph)]
        if explicit:
            return explicit[:3]
        para_tokens = self._tokens(paragraph)
        support: List[str] = []
        for index, row in enumerate(rows, start=1):
            source_text = " ".join(str(row.get(key) or "") for key in ("title", "snippet", "summary", "provider"))
            source_tokens = self._tokens(source_text)
            overlap = len(para_tokens & source_tokens) / max(1, len(para_tokens))
            if overlap >= 0.28:
                support.append(f"S{index}")
            if len(support) >= 3:
                break
        return support

    def _tokens(self, text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]{3,}", str(text or "").lower())}
