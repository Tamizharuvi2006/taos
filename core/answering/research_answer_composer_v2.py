from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .answer_section_planner import build_sections, render_sections
from .answer_strategy import AnswerStrategy, choose_answer_strategy
from .heading_selector import select_schema_key


class ResearchAnswerComposerV2:
    def compose_payload(
        self,
        *,
        query: str,
        intent: str,
        best_supported: str,
        evidence_rows: Iterable[Dict[str, Any]] = (),
        status: str = "",
        answer_mode: str = "",
        caveats: Iterable[str] = (),
        route: str = "",
    ) -> Dict[str, Any]:
        rows = [dict(row or {}) for row in evidence_rows or []]
        strategy = choose_answer_strategy(
            intent=intent,
            status=status,
            evidence={"related": rows, "conflict_detected": any(row.get("conflict") for row in rows)},
            answer_mode=answer_mode,
        )
        schema_key = select_schema_key(
            intent=intent,
            route=route,
            answer_mode=answer_mode,
            evidence_state=answer_mode,
            query=query,
        )
        section_map = self._section_map(
            query=query,
            rows=rows,
            best_supported=best_supported,
            strategy=strategy,
            schema_key=schema_key,
            caveats=caveats,
        )
        sections = build_sections(schema_key=schema_key, section_map=section_map)
        return {
            "schema_key": schema_key,
            "strategy": strategy.value,
            "sections": sections,
            "text": render_sections(sections),
        }

    def compose(
        self,
        *,
        query: str,
        intent: str,
        best_supported: str,
        evidence_rows: Iterable[Dict[str, Any]] = (),
        status: str = "",
        answer_mode: str = "",
        caveats: Iterable[str] = (),
        route: str = "",
    ) -> str:
        payload = self.compose_payload(
            query=query,
            intent=intent,
            best_supported=best_supported,
            evidence_rows=evidence_rows,
            status=status,
            answer_mode=answer_mode,
            caveats=caveats,
            route=route,
        )
        if payload["strategy"] == AnswerStrategy.RUMOUR_UNCONFIRMED_WITH_CONTEXT.value:
            return (
                "I could not confirm the exact claim. "
                f"The best-supported status is: {best_supported}\n\n"
                f"{payload['text']}\n\n"
                f"Answer strategy: {payload['strategy']}"
            ).strip()
        return payload["text"]

    def _section_map(
        self,
        *,
        query: str,
        rows: List[Dict[str, Any]],
        best_supported: str,
        strategy: AnswerStrategy,
        schema_key: str,
        caveats: Iterable[str],
    ) -> Dict[str, Any]:
        source_titles = [str(row.get("title") or row.get("link") or row.get("url") or "").strip() for row in rows[:5]]
        source_titles = [item for item in source_titles if item]
        related = [self._row_summary(row) for row in rows[:3] if self._row_summary(row)]
        caveat_items = [str(item).strip() for item in caveats if str(item).strip()]

        if schema_key == "rumour_verification":
            return {
                "Rumour status": "Not confirmed.",
                "Best-supported status": best_supported,
                "Related evidence": related or ["No reliable related evidence was available."],
                "What this does NOT prove": f"It does not prove the exact claim in '{query}'.",
                "What may be causing confusion": "Related risk, outage, or policy stories may be getting mixed into the rumour.",
                "Confidence": "Low for the exact claim unless a reliable confirming source is found.",
                "Sources checked": source_titles or ["No usable source rows supplied."],
            }
        if schema_key == "related_evidence_only":
            return {
                "Claim status": "Not confirmed.",
                "Closest related evidence": related or ["Only sparse related evidence was available."],
                "What is confirmed": best_supported,
                "What is not confirmed": f"The exact claim in '{query}' is not confirmed.",
                "Likely confusion": "A related story exists, but it is not the same as confirmation of the exact claim.",
                "Confidence": "Low for the exact claim, medium only for the related evidence summary.",
                "Sources": source_titles or ["No usable source rows supplied."],
            }
        if schema_key == "no_usable_evidence":
            return {
                "What I could not verify": f"I could not verify '{query}' from usable evidence.",
                "What I checked": source_titles or ["Search and source-quality checks did not produce a usable source."],
                "Best next checks": caveat_items or ["Retry with the official source-of-record or a narrower query."],
                "Confidence": "Low.",
            }
        if schema_key == "package_version":
            return {
                "Latest version": best_supported,
                "Source-of-record": source_titles or ["No source-of-record row supplied."],
                "Confidence": "High when confirmed by the package registry or official release page.",
            }
        if schema_key == "comparison":
            return {
                "Quick verdict": best_supported,
                "Comparison": related or ["Comparison evidence was not supplied."],
                "Best choice by use case": caveat_items or ["Choose based on your primary use case and constraints."],
                "Trade-offs": ["Different options optimize for different strengths."],
                "Sources": source_titles or ["No usable source rows supplied."],
            }
        if schema_key == "troubleshooting":
            return {
                "Likely cause": best_supported,
                "Fix": caveat_items or ["Apply the most likely fix first and re-test."],
                "Why it works": "It addresses the failure mode indicated by the evidence and error pattern.",
                "If it still fails": ["Re-check logs, exact inputs, and environment assumptions."],
            }
        if schema_key == "explanation":
            return {
                "Simple explanation": best_supported,
                "Example": related[:1] or ["No example supplied."],
                "Common mistake": caveat_items[:1] or ["Mixing the concept with a nearby but different idea."],
                "Quick recap": best_supported,
            }
        if schema_key == "entity_lookup":
            return {
                "Best-supported candidate": best_supported,
                "Why this candidate": related or ["This candidate had the strongest matching evidence."],
                "What is uncertain": caveat_items or ["Some ambiguity remains."],
                "Other possible matches": ["Check alternate people/entities if the name is ambiguous."],
                "Sources checked": source_titles or ["No usable source rows supplied."],
                "Confidence": "Medium unless there is a single unambiguous official source.",
            }
        return {
            "Best-supported answer": best_supported,
            "Why this answer": related or ["This answer follows the strongest available evidence."],
            "Confidence": "Medium unless stronger corroboration is available.",
            "What to treat carefully": caveat_items or ["Treat uncited or weakly supported details as provisional."],
            "Sources": source_titles or ["No usable source rows supplied."],
        }

    def _row_summary(self, row: Dict[str, Any]) -> str:
        title = str(row.get("title") or "").strip()
        snippet = str(row.get("snippet") or row.get("summary") or "").strip()
        if title and snippet:
            return f"{title}: {snippet}"[:280]
        return (title or snippet)[:280]
