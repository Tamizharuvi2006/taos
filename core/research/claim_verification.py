from __future__ import annotations

from typing import Any, Dict, Iterable, List

from taos.core.search.query_planner_v2 import SearchQueryPlannerV2

from .confusion_resolver import ConfusionResolver
from .rumour_status import RumourStatus, status_label


class ClaimVerificationAgent:
    def verify(self, *, query: str, evidence_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        plan = SearchQueryPlannerV2().plan(query)
        rows = [dict(row or {}) for row in evidence_rows or []]
        obj = str(plan.metadata.get("entities", {}).get("product") or plan.metadata.get("entities", {}).get("company") or "").lower()
        country = str(plan.metadata.get("entities", {}).get("country") or "").lower()
        relation = str(plan.metadata.get("relation") or "")
        terms = [term for term in (country, obj) if term]
        resolved = ConfusionResolver().resolve(claim_terms=terms, rows=rows)
        status = self._status(relation=relation, resolved=resolved, rows=rows)
        confidence = self._confidence(status=status, resolved=resolved)
        answer = self.compose(
            query=query,
            status=status,
            best_supported=self._best_supported(status=status, resolved=resolved, rows=rows),
            resolved=resolved,
            confidence=confidence,
        )
        return {
            "status": status.value,
            "status_label": status_label(status),
            "best_supported": self._best_supported(status=status, resolved=resolved, rows=rows),
            "evidence": resolved,
            "confidence": confidence,
            "answer": answer,
            "query_plan": plan.summary(),
        }

    def compose(
        self,
        *,
        query: str,
        status: RumourStatus,
        best_supported: str,
        resolved: Dict[str, Any],
        confidence: str,
    ) -> str:
        confusion = [str(item).strip() for item in resolved.get("possible_confusion") or [] if str(item).strip()]
        lines = [
            f"Rumour status: {status_label(status)}",
            f"Best-supported status: {best_supported}",
            "",
            "What I found:",
            f"- {best_supported}",
            "",
            "Evidence:",
        ]
        lines.append(f"- Confirming evidence: {len(resolved.get('confirming') or [])} source(s)")
        lines.append(f"- Related but not confirming evidence: {len(resolved.get('related') or [])} source(s)")
        lines.append(f"- Contradicting evidence: {len(resolved.get('contradicting') or [])} source(s)")
        lines.extend(["", "What this does NOT prove:"])
        lines.append(f"- It does not prove the exact claim in '{query}'.")
        lines.extend(["", "What may be causing confusion:"])
        for reason in confusion or ["Related stories may be getting mixed into the exact claim."]:
            lines.append(f"- {reason}")
        lines.extend(["", "Bottom line:"])
        lines.append(f"- {best_supported}")
        lines.extend(["", f"Confidence: {confidence}"])
        return "\n".join(lines).strip()

    def _status(self, *, relation: str, resolved: Dict[str, Any], rows: List[Dict[str, Any]]) -> RumourStatus:
        if resolved.get("confirming"):
            return RumourStatus.CONFIRMED
        if resolved.get("contradicting"):
            return RumourStatus.NOT_CONFIRMED
        if resolved.get("related"):
            return RumourStatus.NOT_CONFIRMED
        return RumourStatus.UNCLEAR

    def _best_supported(self, *, status: RumourStatus, resolved: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
        if status == RumourStatus.CONFIRMED:
            return "The exact claim has direct supporting evidence."
        if resolved.get("contradicting"):
            return "The exact claim is not confirmed; available evidence points against treating it as established."
        if resolved.get("related"):
            return "The exact claim is not confirmed, but related evidence may explain why the rumour exists."
        return "The exact claim is unclear because no usable evidence was supplied."

    def _confidence(self, *, status: RumourStatus, resolved: Dict[str, Any]) -> str:
        if resolved.get("contradicting") and resolved.get("related"):
            return "Medium"
        if resolved.get("confirming"):
            return "Medium"
        return "Low"
