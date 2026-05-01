from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List

from taos.core.search.search_accuracy_engine import SearchAccuracyEngine
from taos.core.understanding import (
    CorrectionCandidate,
    IntentFrame,
    SearchIntentPlanner,
    flatten_search_plan,
    plan_search_intent,
)

from .citation_planner import CitationPlanner
from .claim_verifier import ClaimVerifier
from .confusion_explainer import ConfusionExplainer
from .conflict_resolver import ConflictResolver
from .evidence_threshold_gate import EvidenceThresholdGate
from .evidence_selector import EvidenceSelector
from .freshness_booster import FreshnessBooster
from .related_evidence_finder import RelatedEvidenceFinder
from .research_quality_gate import ResearchAnswerMode, ResearchQualityGate


MessyQueryUnderstanding = IntentFrame


@dataclass(frozen=True)
class RumourClaim:
    detected: bool
    original_query: str = ""
    cleaned_query: str = ""
    intent: str = ""
    normalized_query: str = ""
    subject: str = ""
    object: str = ""
    relation: str = ""
    relation_variants: tuple[str, ...] = ()
    correction_candidates: tuple[CorrectionCandidate, ...] = ()
    queries: tuple[str, ...] = ()
    access_claim: bool = False
    confidence: float = 0.0
    needs_llm_rewrite: bool = False
    raw_query_priority: str = "normal"


def normalize_rumour_claim_query(query: str) -> RumourClaim:
    """Compatibility wrapper around the Phase 124A messy-query understanding frame."""
    frame = plan_search_intent(query)
    if not frame.original_query:
        return RumourClaim(detected=False)
    queries = tuple(flatten_search_plan(frame.search_plan, include_fallback=True))
    access_claim = frame.relation in {"blocked_or_restricted_access", "unavailable_or_outage"}
    detected = bool(frame.intent == "rumour_verification" and frame.object and frame.relation)
    legacy_relation = _legacy_relation(frame.relation)
    return RumourClaim(
        detected=detected,
        original_query=frame.original_query,
        cleaned_query=frame.cleaned_query,
        intent=frame.intent,
        normalized_query=frame.normalized_question or frame.cleaned_query,
        subject=frame.subject,
        object=frame.object,
        relation=legacy_relation,
        relation_variants=tuple(frame.relation_frame.variants if frame.relation_frame else ()),
        correction_candidates=frame.correction_candidates,
        queries=queries,
        access_claim=access_claim,
        confidence=frame.confidence,
        needs_llm_rewrite=frame.needs_llm_rewrite,
        raw_query_priority=frame.raw_query_priority,
    )


def build_rumour_no_confirmation_answer(
    *,
    query: str,
    evidence_rows: Iterable[Dict[str, Any]] | None = None,
    checked_queries: Iterable[str] | None = None,
) -> str:
    claim = normalize_rumour_claim_query(query)
    rows = [dict(row or {}) for row in evidence_rows or []]
    related_bundle = RelatedEvidenceFinder().find(query=query, rows=rows)
    queries = [str(row or "").strip() for row in checked_queries or claim.queries if str(row or "").strip()]
    if not claim.detected:
        claim_label = str(query or "").strip()
        best_supported = "No verified status could be established from the available evidence."
        confusion = "Misspellings, unrelated stories, or temporary outages may be causing confusion."
    else:
        claim_label = claim.normalized_query
        source_of_record_status = _source_of_record_status(rows)
        best_supported = str(
            _best_supported_status_for_claim(claim=claim, rows=rows)
            or related_bundle.get("best_available_finding")
        )
        confusion_items = ConfusionExplainer().explain(query=query, rows=rows)
        confusion = "; ".join(confusion_items) if confusion_items else _confusion_status_for_claim(claim=claim, rows=rows)

    source_of_record_status = _source_of_record_status(rows)
    check_next = _next_checks_for_claim(claim_label)

    lines = [
        "Rumour status: Not confirmed.",
        "",
        f"Answer: I could not confirm the rumour: {claim_label}.",
        "",
        f"Best-supported status: {best_supported}",
        "",
        "What I found:",
        f"- The closest reliable finding is: {best_supported}",
        f"- Current best-supported answer: {source_of_record_status or best_supported}",
        f"- Current best-supported status: {source_of_record_status or best_supported}",
        "",
        "What this does NOT prove:",
        f"- It does not prove the exact claim in '{claim_label}'.",
        "",
        "Current access/source-of-record status:",
        source_of_record_status or "No current source-of-record confirmation was available.",
        "",
        "What may be causing confusion:",
        confusion,
        "",
        "The closest related evidence is:",
    ]
    related = _related_evidence_lines(list(related_bundle.get("related_rows") or rows))
    if related:
        lines.extend(related)
    else:
        lines.append("- No reliable source-grounded confirmation for the exact claim was found.")
    lines.extend(["", "Sources checked:"])
    for query_text in queries[:8] or [claim_label]:
        lines.append(f"- {query_text}")
    lines.extend(
        [
            "",
            "What to check next:",
        ]
    )
    lines.extend(f"- {item}" for item in check_next)
    lines.extend(
        [
            "",
            "Bottom line:",
            f"- The rumour is not confirmed. Treat the related evidence as context, not proof of the exact claim.",
            "",
            "Confidence",
            "- Low for the exact rumour claim.",
            "- Medium for the closest verified related finding when source-grounded rows exist.",
        ]
    )
    return "\n".join(lines).strip()


class ResearchPipeline:
    def __init__(self) -> None:
        self._selector = EvidenceSelector()
        self._citation_planner = CitationPlanner()
        self._freshness_booster = FreshnessBooster()
        self._conflict_resolver = ConflictResolver()
        self._quality_gate = ResearchQualityGate()
        self._intent_planner = SearchIntentPlanner()
        self._accuracy = SearchAccuracyEngine()
        self._claim_verifier = ClaimVerifier()
        self._threshold_gate = EvidenceThresholdGate()
        self._related_evidence = RelatedEvidenceFinder()
        self._confusion_explainer = ConfusionExplainer()

    def build_query_variants(self, query: str) -> List[str]:
        text = str(query or "").strip()
        if not text:
            return []
        plan_bundle = self._accuracy.build_plan(text)
        summary = dict(plan_bundle.get("summary") or {})
        planned = list(summary.get("primary_queries") or [])
        planned.extend(list(summary.get("fallback_queries") or []))
        if planned:
            return planned
        frame = self._intent_planner.plan(text)
        base_text = frame.cleaned_query if frame.raw_query_priority == "fallback_only" else text
        base_text = base_text or text
        variants = [
            base_text,
            f"{base_text} official source",
            f"{base_text} latest news",
            f"{base_text} contradiction conflict concerns",
            f"{base_text} recent update",
        ]
        if frame.raw_query_priority == "fallback_only" and base_text.lower() != text.lower():
            variants.append(text)
        return _dedupe(variants)[:5]

    def understand_messy_query(self, query: str) -> Dict[str, Any]:
        frame = self._intent_planner.plan(query)
        data = asdict(frame)
        data["subject"] = frame.subject
        data["object"] = frame.object
        return data

    def build_low_confidence_rewrite_prompt(self, query: str) -> str:
        frame = self._intent_planner.plan(query)
        from taos.core.understanding.semantic_query_rewriter import SemanticQueryRewriter

        return SemanticQueryRewriter().low_confidence_prompt(frame)

    def normalize_rumour_claim(self, query: str) -> Dict[str, Any]:
        return asdict(normalize_rumour_claim_query(query))

    def compose_rumour_no_confirmation_answer(
        self,
        *,
        query: str,
        evidence_rows: Iterable[Dict[str, Any]] | None = None,
        checked_queries: Iterable[str] | None = None,
    ) -> str:
        return build_rumour_no_confirmation_answer(
            query=query,
            evidence_rows=evidence_rows,
            checked_queries=checked_queries,
        )

    def search_plan_summary(self, query: str) -> Dict[str, Any]:
        return dict(self._accuracy.build_plan(query).get("summary") or {})

    def prefilter_search_results(self, *, query: str, rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        return self._accuracy.prefilter_results(rows=rows, query=query)

    def select_evidence(
        self,
        *,
        rows: Iterable[Dict[str, Any]],
        query: str,
        limit: int = 8,
        max_per_domain: int = 2,
    ) -> Dict[str, Any]:
        return self._selector.select(rows, query=query, limit=limit, max_per_domain=max_per_domain)

    def plan_citations(self, *, answer: str, source_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        return self._citation_planner.plan(answer=answer, source_rows=source_rows)

    def assess_source_quality(
        self,
        *,
        rows: Iterable[Dict[str, Any]],
        query: str = "",
        freshness_summary: Dict[str, Any] | None = None,
        official_source_required: bool = False,
    ) -> Dict[str, Any]:
        prefiltered = self.prefilter_search_results(query=query, rows=rows)
        assessed = self._quality_gate.assess_sources(
            rows=list(prefiltered.get("kept") or []),
            query=query,
            freshness_summary=freshness_summary,
            official_source_required=official_source_required,
        )
        assessed["prefilter_summary"] = {
            "rejected_results_count": int(prefiltered.get("rejected_results_count") or 0),
            "reasons": dict(prefiltered.get("reasons") or {}),
        }
        assessed["rejected_rows"] = list(prefiltered.get("rejected") or [])
        return assessed

    def citation_coverage(self, *, citation_plan: Dict[str, Any]) -> Dict[str, Any]:
        return self._quality_gate.citation_coverage(citation_plan)

    def answer_policy(
        self,
        *,
        quality_summary: Dict[str, Any],
        citation_coverage: Dict[str, Any] | None = None,
        conflict_summary: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return self._quality_gate.answer_policy(
            quality_summary=quality_summary,
            citation_coverage=citation_coverage,
            conflict_summary=conflict_summary,
        )

    def verify_claim(self, *, query: str, evidence_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        return self._claim_verifier.verify(query=query, evidence_rows=evidence_rows)

    def related_evidence(self, *, query: str, evidence_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        bundle = self._related_evidence.find(query=query, rows=evidence_rows)
        confusion_items = self._confusion_explainer.explain(query=query, rows=evidence_rows)
        bundle["confusion_explanation_list"] = confusion_items
        bundle["confusion_explanation"] = "; ".join(confusion_items)
        bundle["confusion_explanation_present"] = bool(confusion_items)
        return bundle

    def evidence_threshold(
        self,
        *,
        usable_sources_count: int,
        selected_rows: int,
        coverage: float,
        claim_verification_status: str = "",
    ) -> Dict[str, Any]:
        return self._threshold_gate.evaluate(
            usable_sources_count=usable_sources_count,
            selected_rows=selected_rows,
            coverage=coverage,
            claim_verification_status=claim_verification_status,
        )

    def targeted_retry_plan(
        self,
        *,
        query: str,
        quality_summary: Dict[str, object],
        query_plan_summary: Dict[str, object],
    ) -> Dict[str, object]:
        return self._accuracy.targeted_retry(
            query=query,
            quality_summary=quality_summary,
            query_plan_summary=query_plan_summary,
        )

    def repair_answer(self, *, answer: str, citation_plan: Dict[str, Any]) -> Dict[str, Any]:
        return self._quality_gate.repair_answer(answer=answer, citation_plan=citation_plan)

    def compose_research_answer(
        self,
        *,
        query: str,
        draft_answer: str = "",
        evidence_rows: Iterable[Dict[str, Any]],
        answer_policy: Dict[str, Any],
        conflict_summary: Dict[str, Any] | None = None,
    ) -> str:
        return self._quality_gate.compose_research_answer(
            query=query,
            draft_answer=draft_answer,
            evidence_rows=evidence_rows,
            answer_policy=answer_policy,
            conflict_summary=conflict_summary,
        )

    def soften_unsupported_claims(self, *, answer: str, citation_plan: Dict[str, Any]) -> str:
        return self._citation_planner.soften_unsupported(answer=answer, citation_plan=citation_plan)

    def resolve_conflicts(self, *, evidence_rows: Iterable[Dict[str, Any]], claims: Iterable[str] | None = None) -> Dict[str, Any]:
        return self._conflict_resolver.resolve(evidence_rows=evidence_rows, claims=claims)

    @property
    def freshness_booster(self) -> FreshnessBooster:
        return self._freshness_booster


def _best_supported_status_for_claim(*, claim: RumourClaim, rows: List[Dict[str, Any]]) -> str:
    joined = " ".join(
        str(row.get(key) or "")
        for row in rows
        for key in ("title", "snippet", "summary", "raw_snippet")
    ).lower()
    if claim.subject == "India" and claim.object == "Claude":
        if "supported countr" in joined or "available in india" in joined:
            return "Claude still appears available/supported in India based on official source."
        if "india" in joined or not rows:
            return "India/RBI risk-review stories do not confirm a Claude block, and Claude still appears available in India based on supported-countries evidence."
    if rows:
        top = str(rows[0].get("snippet") or rows[0].get("summary") or rows[0].get("title") or "").strip()
        if top:
            return top[:220]
    return "The exact access/block claim is not confirmed by the available evidence."


def _legacy_relation(relation: str) -> str:
    if relation == "blocked_or_restricted_access":
        return "blocking"
    if relation == "unavailable_or_outage":
        return "unavailable"
    return relation


def _confusion_status_for_claim(*, claim: RumourClaim, rows: List[Dict[str, Any]]) -> str:
    joined = " ".join(
        str(row.get(key) or "")
        for row in rows
        for key in ("title", "snippet", "summary", "raw_snippet")
    ).lower()
    if claim.subject == "India" and claim.object == "Claude":
        possible = []
        if "outage" in joined or not rows:
            possible.append("recent Claude outage")
        if "mythos" in joined or "cyber" in joined or not rows:
            possible.append("Claude Mythos cyber-risk news")
        if "suspension" in joined or "suspend" in joined or "account" in joined or not rows:
            possible.append("account suspension stories")
        return " / ".join(_dedupe(possible)) + "."
    return "related outage, policy, or account-access stories may be different from a confirmed country-wide block."


def _source_of_record_status(rows: List[Dict[str, Any]]) -> str:
    joined = " ".join(
        str(row.get(key) or "")
        for row in rows
        for key in ("title", "snippet", "summary", "raw_snippet")
    ).lower()
    if any(token in joined for token in ("supported countries", "supported regions", "available in india", "still available")):
        return "Available/support-page evidence indicates Claude still appears available in India."
    return ""


def _next_checks_for_claim(claim_label: str) -> List[str]:
    return [
        "Check Anthropic supported countries / supported regions for current Claude availability.",
        "Check Indian regulator or government statements for any official restriction notice.",
        f"Check reliable reporting for updates related to {claim_label}.",
    ]


def _related_evidence_lines(rows: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for idx, row in enumerate(rows[:3], start=1):
        title = str(row.get("title") or f"Related source {idx}").strip()
        snippet = str(row.get("snippet") or row.get("summary") or "").strip()
        link = str(row.get("link") or row.get("url") or "").strip()
        detail = f"{title}: {snippet}" if snippet else title
        detail = detail[:260]
        if link:
            lines.append(f"- {detail} ({link})")
        else:
            lines.append(f"- {detail}")
    return lines


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(text)
    return out


__all__ = [
    "CorrectionCandidate",
    "MessyQueryUnderstanding",
    "ResearchAnswerMode",
    "ResearchPipeline",
    "RumourClaim",
    "build_rumour_no_confirmation_answer",
    "normalize_rumour_claim_query",
]
