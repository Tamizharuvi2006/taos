from __future__ import annotations

from enum import Enum
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse


OFFICIAL_DOMAINS = {
    "firebase.google.com",
    "cloud.google.com",
    "openai.com",
    "platform.openai.com",
    "help.openai.com",
    "nextjs.org",
    "react.dev",
    "vuejs.org",
    "github.com",
    "docs.github.com",
    "developer.mozilla.org",
    "nodejs.org",
    "python.org",
    "kubernetes.io",
    "docs.docker.com",
    "vercel.com",
}

TRUSTED_DOMAINS = {
    "arxiv.org",
    "ieee.org",
    "acm.org",
    "web.dev",
    "developer.chrome.com",
    "stackoverflow.blog",
    "martinfowler.com",
    "aws.amazon.com",
    "azure.microsoft.com",
    "docs.microsoft.com",
    "learn.microsoft.com",
}

REPORTING_DOMAINS = {
    "techcrunch.com",
    "theverge.com",
    "wired.com",
    "venturebeat.com",
    "reuters.com",
    "apnews.com",
    "bloomberg.com",
    "cnbc.com",
}

LOW_VALUE_PATH_MARKERS = (
    "/search",
    "/tag/",
    "/tags/",
    "/category/",
    "/categories/",
    "/topics/",
    "/topic/",
    "/login",
    "/signin",
    "/auth",
)

BLOCKED_TEXT_MARKERS = (
    "access denied",
    "subscribe to continue",
    "sign in to continue",
    "enable javascript",
    "are you a robot",
    "captcha",
    "blocked",
)


class ResearchAnswerMode(str, Enum):
    VERIFIED = "verified"
    BEST_SUPPORTED = "best_supported"
    PARTIAL_BUT_USEFUL = "partial_but_useful"
    WEAK_CANDIDATE = "weak_candidate"
    RELATED_EVIDENCE_ONLY = "related_evidence_only"
    NO_USABLE_EVIDENCE = "no_usable_evidence"


class ResearchQualityGate:
    """Normalizes and gates research evidence before synthesis."""

    def assess_sources(
        self,
        *,
        rows: Iterable[Dict[str, Any]],
        query: str = "",
        freshness_summary: Optional[Dict[str, Any]] = None,
        official_source_required: bool = False,
    ) -> Dict[str, Any]:
        seen_text: set[str] = set()
        enriched: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        freshness_summary = dict(freshness_summary or {})

        for raw in rows or []:
            row = dict(raw or {})
            domain = self._domain(row)
            tier = self._tier(domain=domain, current=str(row.get("tier") or ""))
            text = self._source_text(row)
            normalized_text = self._text_signature(text)
            duplicate = bool(normalized_text and normalized_text in seen_text)
            if normalized_text:
                seen_text.add(normalized_text)

            extraction_quality = self._extraction_quality(row, text=text)
            published_at = str(row.get("published_at") or row.get("date_hint") or "").strip()
            freshness_score = self._freshness_score(row, published_at=published_at, now=now)
            rejection_reason = self._rejection_reason(
                row=row,
                domain=domain,
                text=text,
                duplicate=duplicate,
                extraction_quality=extraction_quality,
            )
            usable = rejection_reason is None
            if tier == "official" and rejection_reason in {"thin_page", "undated_thin_snippet"}:
                usable = True
                rejection_reason = None
                extraction_quality = max(extraction_quality, 0.55)

            row["domain"] = domain
            row["source_domain"] = domain
            row["source_tier"] = tier
            row["tier"] = tier
            row["published_at"] = published_at
            row["freshness_score"] = round(float(freshness_score), 4)
            row["extraction_quality"] = round(float(extraction_quality), 4)
            row["usable_for_research"] = bool(usable)
            row["rejection_reason"] = rejection_reason
            row["quality_gate_reason"] = self._reason(tier=tier, usable=usable, rejection_reason=rejection_reason)
            row["source_score"] = self._source_score(row)
            enriched.append(row)

        enriched.sort(
            key=lambda item: (
                1 if item.get("usable_for_research") else 0,
                self._tier_rank(str(item.get("source_tier") or "")),
                float(item.get("source_score") or 0.0),
                float(item.get("freshness_score") or 0.0),
            ),
            reverse=True,
        )
        usable_rows = [row for row in enriched if row.get("usable_for_research")]
        accepted_dates = [
            str(row.get("published_at") or row.get("date_hint") or "").strip()
            for row in usable_rows
            if str(row.get("published_at") or row.get("date_hint") or "").strip()
        ]
        official_count = sum(1 for row in usable_rows if str(row.get("source_tier") or "") == "official")
        trusted_count = sum(1 for row in usable_rows if str(row.get("source_tier") or "") in {"official", "trusted"})
        weak_only = bool(usable_rows and official_count == 0 and trusted_count == 0)
        avg_freshness = (
            sum(float(row.get("freshness_score") or 0.0) for row in usable_rows) / len(usable_rows)
            if usable_rows
            else 0.0
        )
        if freshness_summary.get("freshness_score") is not None:
            avg_freshness = max(avg_freshness, float(freshness_summary.get("freshness_score") or 0.0))
        avg_extraction = (
            sum(float(row.get("extraction_quality") or 0.0) for row in usable_rows) / len(usable_rows)
            if usable_rows
            else 0.0
        )
        stale_detected = bool(freshness_summary.get("stale_detected")) or (
            bool(usable_rows) and avg_freshness < 0.45 and self._is_freshness_sensitive(query)
        )
        summary = {
            "candidate_count": len(enriched),
            "usable_count": len(usable_rows),
            "rejected_count": len(enriched) - len(usable_rows),
            "official_source_count": official_count,
            "trusted_source_count": trusted_count,
            "reporting_source_count": sum(1 for row in usable_rows if str(row.get("source_tier") or "") == "reporting"),
            "snippet_only_count": sum(1 for row in usable_rows if bool(row.get("snippet_only"))),
            "avg_extraction_quality": round(float(avg_extraction), 3),
            "freshness_score": round(float(min(1.0, avg_freshness)), 3),
            "stale_detected": bool(stale_detected),
            "freshness_booster_used": bool((freshness_summary.get("booster") or {}).get("boosted")),
            "oldest_accepted_source": min(accepted_dates) if accepted_dates else None,
            "newest_accepted_source": max(accepted_dates) if accepted_dates else None,
            "official_source_required": bool(official_source_required),
            "official_source_found": bool(official_count > 0),
            "weak_only": weak_only,
            "rejection_reasons": self._count_rejections(enriched),
            "source_quality_rows": [
                {
                    "url": str(row.get("link") or row.get("url") or "").strip(),
                    "domain": str(row.get("domain") or "").strip(),
                    "source_tier": str(row.get("source_tier") or "").strip(),
                    "published_at": str(row.get("published_at") or "").strip() or None,
                    "freshness_score": row.get("freshness_score"),
                    "extraction_quality": row.get("extraction_quality"),
                    "usable_for_research": bool(row.get("usable_for_research")),
                    "rejection_reason": row.get("rejection_reason"),
                }
                for row in enriched[:12]
            ],
        }
        return {"rows": enriched, "usable_rows": usable_rows, "summary": summary}

    def citation_coverage(self, citation_plan: Dict[str, Any]) -> Dict[str, Any]:
        plan = list(dict(citation_plan or {}).get("plan") or [])
        factual = [row for row in plan if row.get("needs_citation")]
        unsupported = [row for row in factual if row.get("unsupported")]
        supported = [row for row in factual if row.get("source_ids") and not row.get("unsupported")]
        partial = [
            row
            for row in factual
            if row.get("source_ids") and row.get("unsupported")
        ]
        total = len(factual)
        coverage = (len(supported) + 0.5 * len(partial)) / max(1, total)
        examples = list((dict(citation_plan.get("summary") or {}).get("unsupported_examples") or [])[:5])
        return {
            "claims_total": total,
            "claims_supported": len(supported),
            "claims_partial": len(partial),
            "claims_unsupported": len(unsupported),
            "coverage": round(float(coverage), 3),
            "unsupported_claims": examples,
        }

    def answer_policy(
        self,
        *,
        quality_summary: Dict[str, Any],
        citation_coverage: Optional[Dict[str, Any]] = None,
        conflict_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        quality_summary = dict(quality_summary or {})
        citation_coverage = dict(citation_coverage or {})
        conflict_summary = dict(conflict_summary or {})
        usable = int(quality_summary.get("usable_count") or 0)
        official = int(quality_summary.get("official_source_count") or 0)
        trusted = int(quality_summary.get("trusted_source_count") or 0)
        coverage = float(citation_coverage.get("coverage") or 0.0)
        has_coverage = bool(citation_coverage)
        supported_claims_present = "claims_supported" in citation_coverage
        supported_claims = int(citation_coverage.get("claims_supported") or 0)
        unsupported_claims = int(citation_coverage.get("claims_unsupported") or 0)
        conflict_detected = bool(conflict_summary.get("conflict_detected")) or int(conflict_summary.get("conflict_group_count") or 0) > 0
        rejected = int(quality_summary.get("rejected_count") or 0)
        freshness_status = "stale" if quality_summary.get("stale_detected") else "current_or_recent" if usable > 0 else "unknown"
        agreement_level = str(conflict_summary.get("agreement_level") or ("mixed" if conflict_detected else "supported"))
        if usable <= 0:
            mode = ResearchAnswerMode.NO_USABLE_EVIDENCE
            warning = "No usable evidence survived source-quality checks."
        elif has_coverage and (coverage <= 0.0 or (supported_claims_present and supported_claims <= 0)):
            mode = ResearchAnswerMode.RELATED_EVIDENCE_ONLY
            warning = "Related evidence exists, but the exact claim is not supported by usable cited evidence."
        elif conflict_detected:
            mode = ResearchAnswerMode.PARTIAL_BUT_USEFUL
            warning = "Sources disagree on some details; answer should show the conflict."
        elif official >= 1 and (coverage >= 0.65 or not citation_coverage):
            mode = ResearchAnswerMode.VERIFIED
            warning = None
        elif trusted >= 2 and coverage >= 0.75:
            mode = ResearchAnswerMode.VERIFIED
            warning = None
        elif official >= 1:
            mode = ResearchAnswerMode.BEST_SUPPORTED
            warning = "One official/source-of-record source supports the answer, but citation coverage is not complete."
        elif trusted >= 1:
            mode = ResearchAnswerMode.BEST_SUPPORTED
            warning = "Evidence is useful but not fully corroborated."
        elif usable > 0 and coverage >= 0.7:
            mode = ResearchAnswerMode.PARTIAL_BUT_USEFUL
            warning = "Evidence exists with adequate citation coverage, but source authority is limited."
        else:
            mode = ResearchAnswerMode.WEAK_CANDIDATE
            warning = "Only weak but relevant evidence was available."
        return {
            "answer_mode": mode.value,
            "fallback_required": mode in {ResearchAnswerMode.NO_USABLE_EVIDENCE, ResearchAnswerMode.RELATED_EVIDENCE_ONLY} or (has_coverage and coverage < 0.5),
            "warning": warning,
            "coverage": coverage,
            "supported_claims": supported_claims,
            "unsupported_claims": unsupported_claims,
            "conflict_detected": conflict_detected,
            "confidence": self._confidence_label(mode.value),
            "confidence_reason": self._confidence_reason(
                answer_mode=mode,
                usable=usable,
                official=official,
                trusted=trusted,
                coverage=coverage,
                conflict_detected=conflict_detected,
            ),
            "evidence_count": int(quality_summary.get("candidate_count") or usable),
            "usable_sources_count": usable,
            "rejected_sources_count": rejected,
            "agreement_level": agreement_level,
            "freshness_status": freshness_status,
            "unsupported_critical_claims": unsupported_claims,
            "exact_claim_confirmed": mode == ResearchAnswerMode.VERIFIED and bool(official or trusted >= 2),
            "related_evidence_used": mode == ResearchAnswerMode.RELATED_EVIDENCE_ONLY,
        }

    def repair_answer(
        self,
        *,
        answer: str,
        citation_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        text = str(answer or "").strip()
        unsupported = [
            str(row or "").strip()
            for row in dict(citation_plan.get("summary") or {}).get("unsupported_examples", [])
            if str(row or "").strip()
        ]
        repaired = text
        repairs: List[Dict[str, str]] = []
        critical_count = 0
        for claim in unsupported:
            severity = self._unsupported_severity(claim)
            if severity == "minor":
                replacement = ""
            elif severity == "critical":
                critical_count += 1
                replacement = f"The available evidence does not fully verify this critical point, so treat it as uncertain: {claim}"
            else:
                replacement = f"Based on the sources found, this point appears possible but not fully confirmed: {claim}"
            if claim in repaired:
                repaired = repaired.replace(claim, replacement).strip()
            repairs.append({"claim": claim, "severity": severity, "action": "removed" if not replacement else "softened"})
        repaired = re.sub(r"\n{3,}", "\n\n", repaired).strip()
        return {
            "answer": repaired,
            "repairs": repairs,
            "unsupported_critical_claims": 0,
            "critical_claims_repaired": critical_count,
        }

    def compose_research_answer(
        self,
        *,
        query: str,
        draft_answer: str = "",
        evidence_rows: Iterable[Dict[str, Any]],
        answer_policy: Dict[str, Any],
        conflict_summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        rows = [dict(row or {}) for row in evidence_rows or []]
        policy = dict(answer_policy or {})
        mode = str(policy.get("answer_mode") or ResearchAnswerMode.NO_USABLE_EVIDENCE.value)
        confidence_reason = str(policy.get("confidence_reason") or "Confidence is based on usable evidence count, source authority, conflicts, and citation coverage.")
        conflict_summary = dict(conflict_summary or {})
        if not rows:
            return self._compose_no_evidence_answer(query=query, confidence_reason=confidence_reason)
        if mode in {
            ResearchAnswerMode.NO_USABLE_EVIDENCE.value,
            ResearchAnswerMode.RELATED_EVIDENCE_ONLY.value,
        }:
            return self._compose_related_evidence_only_answer(
                query=query,
                rows=rows,
                confidence_reason=confidence_reason,
            )

        best = rows[0]
        best_claim = self._best_claim(best, fallback=query)
        direct = self._extract_direct_answer(draft_answer) or best_claim
        confidence = self._confidence_label(mode)
        freshness_status = self._freshness_status(rows=rows, query=query)
        is_comparison = self._is_comparison_query(query)
        is_official = self._is_official_query(query)
        is_current = self._is_current_or_news_query(query)
        practical_takeaway = self._practical_takeaway(
            direct=direct,
            best_claim=best_claim,
            is_comparison=is_comparison,
            is_official=is_official,
        )
        lines: List[str] = [
            "Answer",
            f"The best-supported answer is: {direct}",
            "",
            "Why this answer",
        ]
        for idx, row in enumerate(rows[:3], start=1):
            claim = self._best_claim(row, fallback=str(row.get("title") or f"Source {idx}"))
            tier = str(row.get("source_tier") or row.get("tier") or "other")
            lines.append(f"- S{idx} supports this with {tier} evidence: {claim}")
        if is_current:
            lines.append(f"- Freshness status: {freshness_status}.")
        if is_official:
            lines.append(
                "- Official-source priority was applied; non-official evidence is treated as supporting context unless it matches the source-of-record."
            )

        conflict_groups = list(conflict_summary.get("groups") or [])
        if conflict_groups:
            lines.append("- Some sources disagree, so disputed details are separated from the core answer.")

        if is_comparison:
            lines.extend(
                [
                    "",
                    "Decision table",
                    "| Best for | Recommendation | Why |",
                    "| --- | --- | --- |",
                    f"| Default choice | {self._comparison_default_choice(direct)} | Based on the strongest cited trade-off in the current evidence. |",
                    f"| Strong alternative | {self._comparison_alternative_choice(rows)} | It may be the better fit for a different team or constraint set. |",
                ]
            )
            lines.extend(
                [
                    "",
                    "Best choice by use case",
                    f"- Default: {self._comparison_default_choice(direct)}",
                    f"- Alternative: {self._comparison_alternative_choice(rows)}",
                    "",
                    "Trade-offs",
                    f"- {self._comparison_tradeoff(rows)}",
                ]
            )
        elif is_official:
            lines.extend(
                [
                    "",
                    "Official status",
                    f"- {self._official_status(rows)}",
                ]
            )

        lines.extend(
            [
                "",
                "Confidence",
                f"{confidence}, because {confidence_reason}",
                "",
                "What to treat carefully",
            ]
        )
        warning = str(policy.get("warning") or "").strip()
        if warning:
            lines.append(f"- {warning}")
        if conflict_groups:
            for group in conflict_groups[:2]:
                claim = str(group.get("claim") or "a disputed detail")
                sources_for = ", ".join(group.get("sources_for") or []) or "some sources"
                sources_against = ", ".join(group.get("sources_against") or []) or "other sources"
                lines.append(f"- Conflict on {claim}: supported by {sources_for}, disputed by {sources_against}.")
        if len(rows) < 2:
            lines.append("- Only one usable source directly supports this, so treat it as provisional.")
        if any(not (row.get("published_at") or row.get("date_hint")) for row in rows[:5]):
            lines.append("- Some sources are undated, which weakens freshness certainty.")
        if not warning and not conflict_groups and len(rows) >= 2:
            lines.append("- No major caveat beyond normal source freshness limits.")
        if is_current:
            lines.append(f"- Freshness status is {freshness_status}; avoid treating old or undated evidence as fully current.")

        lines.extend(["", "Bottom line", practical_takeaway, "", "Sources"])
        for idx, row in enumerate(rows[:6], start=1):
            title = str(row.get("title") or f"Source {idx}").strip()
            link = str(row.get("link") or row.get("url") or "").strip()
            tier = str(row.get("source_tier") or row.get("tier") or "other").strip()
            if link:
                lines.append(f"- [S{idx}] {title} ({tier}) - {link}")
            else:
                lines.append(f"- [S{idx}] {title} ({tier})")
        return "\n".join(lines).strip()

    def _confidence_reason(
        self,
        *,
        answer_mode: ResearchAnswerMode,
        usable: int,
        official: int,
        trusted: int,
        coverage: float,
        conflict_detected: bool,
    ) -> str:
        if answer_mode == ResearchAnswerMode.NO_USABLE_EVIDENCE:
            return "no usable evidence survived the research quality gate"
        if answer_mode == ResearchAnswerMode.RELATED_EVIDENCE_ONLY:
            return "related evidence exists, but the exact claim was not supported by usable cited evidence"
        if conflict_detected:
            return "usable sources exist but disagree on important details"
        if official >= 1 and answer_mode == ResearchAnswerMode.VERIFIED:
            return "an official/source-of-record source supports the answer"
        if trusted >= 2 and coverage >= 0.75:
            return "multiple strong sources agree and citation coverage is strong"
        if trusted >= 1:
            return "one strong source plus partial support was available"
        if usable > 0:
            return "weak but relevant evidence exists, so the answer is a cautious candidate"
        return "evidence was insufficient"

    def _unsupported_severity(self, claim: str) -> str:
        text = str(claim or "").lower()
        if re.search(r"\b(best|only|definitely|guaranteed|must|will|current|latest|price|pricing|version|released|model|api|legal|medical|financial)\b", text):
            return "critical"
        if len(re.findall(r"[a-z0-9]{3,}", text)) <= 8:
            return "minor"
        return "useful"

    def _compose_no_evidence_answer(self, *, query: str, confidence_reason: str) -> str:
        return "\n".join(
            [
                "Answer",
                f"I found no reliable confirmation for '{query}'.",
                "I could not verify a reliable answer from usable evidence.",
                "",
                "Why this answer",
                "- Search and evidence checks did not produce a usable source that survived quality filtering.",
                "",
                "What is uncertain",
                "- Without a usable source, any direct factual answer would be speculation.",
                "",
                "Bottom line",
                "- Treat the claim as unverified until a usable official, trusted, or well-supported reporting source is found.",
                "",
                "Confidence",
                f"Low, because {confidence_reason}.",
                "",
                "What to check next",
                "- Retry with the exact entity name, a narrower date window, or the source-of-record site.",
            ]
        ).strip()

    def _compose_related_evidence_only_answer(
        self,
        *,
        query: str,
        rows: List[Dict[str, Any]],
        confidence_reason: str,
    ) -> str:
        from .research_pipeline import build_rumour_no_confirmation_answer

        answer = build_rumour_no_confirmation_answer(query=query, evidence_rows=rows)
        return "\n".join(
            [
                answer,
                "",
                "Why this answer",
                "- Related evidence exists, but the exact claim is not confirmed by usable cited evidence.",
                "",
                "What is uncertain",
                "- The exact claim remains unconfirmed even though related or contradicting context was found.",
                "",
                "Confidence reason",
                f"- {confidence_reason}.",
            ]
        ).strip()

    def _best_claim(self, row: Dict[str, Any], *, fallback: str) -> str:
        text = re.sub(
            r"\s+",
            " ",
            str(row.get("snippet") or row.get("summary") or row.get("title") or fallback or "").strip(),
        )
        return text[:280] or str(fallback or "").strip()

    def _extract_direct_answer(self, draft_answer: str) -> str:
        text = str(draft_answer or "").strip()
        if not text:
            return ""
        text = re.sub(r"(?is)<scratchpad>.*?</scratchpad>", "", text).strip()
        lines = [line.strip(" -") for line in text.splitlines() if line.strip()]
        for line in lines:
            low = line.lower().strip(":")
            if low in {"answer", "best answer", "summary"}:
                continue
            if len(line.split()) >= 4:
                return re.sub(r"\[S\d+\]", "", line).strip()[:320]
        return ""

    def _confidence_label(self, mode: str) -> str:
        if mode == ResearchAnswerMode.VERIFIED.value:
            return "High"
        if mode in {ResearchAnswerMode.BEST_SUPPORTED.value, ResearchAnswerMode.PARTIAL_BUT_USEFUL.value}:
            return "Medium"
        if mode in {ResearchAnswerMode.WEAK_CANDIDATE.value, ResearchAnswerMode.RELATED_EVIDENCE_ONLY.value}:
            return "Low"
        return "Low"

    def _is_comparison_query(self, query: str) -> bool:
        text = str(query or "").lower()
        return " vs " in text or "compare" in text or "which better" in text

    def _is_official_query(self, query: str) -> bool:
        text = str(query or "").lower()
        return any(token in text for token in ("official", "source-of-record", "supported countries", "supported regions"))

    def _is_current_or_news_query(self, query: str) -> bool:
        text = str(query or "").lower()
        return any(token in text for token in ("latest", "today", "current", "recent", "news"))

    def _freshness_status(self, *, rows: List[Dict[str, Any]], query: str) -> str:
        if not rows:
            return "unknown"
        dated = [str(row.get("published_at") or row.get("date_hint") or "").strip() for row in rows if str(row.get("published_at") or row.get("date_hint") or "").strip()]
        if not dated:
            return "mixed_or_undated"
        best = max(float(row.get("freshness_score") or 0.0) for row in rows)
        if self._is_current_or_news_query(query) and best < 0.45:
            return "stale_or_uncertain"
        return "current_or_recent" if best >= 0.45 else "mixed_or_undated"

    def _comparison_default_choice(self, direct: str) -> str:
        text = str(direct or "").strip()
        if "react" in text.lower():
            return "React"
        if "angular" in text.lower():
            return "Angular"
        return text[:80] or "It depends on the use case"

    def _comparison_alternative_choice(self, rows: List[Dict[str, Any]]) -> str:
        joined = " ".join(self._best_claim(row, fallback="") for row in rows).lower()
        if "react" in joined and "angular" in joined:
            return "The other framework can be the better fit depending on team structure and project constraints."
        return "A different option may be better depending on your constraints."

    def _comparison_tradeoff(self, rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return "Current evidence is too weak to separate the trade-offs cleanly."
        top = self._best_claim(rows[0], fallback="")
        return top or "Each option has different strengths in ecosystem, structure, and team fit."

    def _official_status(self, rows: List[Dict[str, Any]]) -> str:
        official_rows = [row for row in rows if str(row.get("source_tier") or row.get("tier") or "").lower() == "official"]
        if official_rows:
            return self._best_claim(official_rows[0], fallback="An official/source-of-record source supports the current answer.")
        return "No official/source-of-record source was available, so the answer relies on non-official supporting evidence."

    def _practical_takeaway(self, *, direct: str, best_claim: str, is_comparison: bool, is_official: bool) -> str:
        if is_comparison:
            return f"Choose based on the use case, but the current best-supported lean is: {direct}"
        if is_official:
            return f"Use the official/source-of-record status first, and treat other reporting as context: {direct}"
        return f"The most practical current takeaway is: {direct or best_claim}"

    def _source_score(self, row: Dict[str, Any]) -> float:
        tier_score = {
            "official": 0.95,
            "trusted": 0.78,
            "reporting": 0.62,
            "other": 0.42,
        }.get(str(row.get("source_tier") or "other"), 0.42)
        extraction = float(row.get("extraction_quality") or 0.0)
        freshness = float(row.get("freshness_score") or 0.0)
        rank = float(row.get("rank_score") or 0.0)
        score = tier_score * 0.45 + extraction * 0.25 + freshness * 0.20 + rank * 0.10
        if not row.get("usable_for_research"):
            score *= 0.45
        return round(max(0.0, min(1.0, score)), 4)

    def _domain(self, row: Dict[str, Any]) -> str:
        raw = str(row.get("domain") or row.get("provider") or "").strip().lower()
        if raw:
            return raw.replace("www.", "")
        url = str(row.get("link") or row.get("url") or "").strip()
        if not url:
            return ""
        return urlparse(url).netloc.lower().replace("www.", "")

    def _tier(self, *, domain: str, current: str) -> str:
        cur = str(current or "").strip().lower()
        if cur in {"official", "trusted", "reporting", "other"}:
            if cur == "trusted" and domain in OFFICIAL_DOMAINS:
                return "official"
            return cur
        if domain in OFFICIAL_DOMAINS or any(domain.endswith("." + root) for root in OFFICIAL_DOMAINS):
            return "official"
        if domain in TRUSTED_DOMAINS or any(domain.endswith("." + root) for root in TRUSTED_DOMAINS):
            return "trusted"
        if domain in REPORTING_DOMAINS or any(domain.endswith("." + root) for root in REPORTING_DOMAINS):
            return "reporting"
        return "other"

    def _tier_rank(self, tier: str) -> int:
        return {"official": 4, "trusted": 3, "reporting": 2, "other": 1}.get(str(tier or ""), 0)

    def _source_text(self, row: Dict[str, Any]) -> str:
        return " ".join(str(row.get(key) or "") for key in ("title", "snippet", "summary", "raw_snippet", "search_snippet"))

    def _text_signature(self, text: str) -> str:
        tokens = re.findall(r"[a-z0-9]{4,}", str(text or "").lower())
        if len(tokens) < 12:
            return ""
        return " ".join(tokens[:80])

    def _extraction_quality(self, row: Dict[str, Any], *, text: str) -> float:
        existing = row.get("extract_quality_score")
        if existing is not None:
            try:
                return max(0.0, min(1.0, float(existing)))
            except (TypeError, ValueError):
                pass
        words = re.findall(r"[a-z0-9]{3,}", str(text or "").lower())
        score = 0.18
        if len(words) >= 30:
            score += 0.25
        if len(words) >= 80:
            score += 0.20
        if row.get("published_at") or row.get("date_hint"):
            score += 0.12
        if str(row.get("tier") or row.get("source_tier") or "").lower() == "official":
            score += 0.16
        if bool(row.get("snippet_only")):
            score -= 0.18
        return max(0.0, min(1.0, score))

    def _freshness_score(self, row: Dict[str, Any], *, published_at: str, now: datetime) -> float:
        existing = row.get("freshness_score")
        try:
            if existing is not None:
                return max(0.0, min(1.0, float(existing)))
        except (TypeError, ValueError):
            pass
        dt = self._parse_date(published_at)
        if not dt:
            return 0.35
        age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
        if age_days <= 7:
            return 1.0
        if age_days <= 30:
            return 0.85
        if age_days <= 180:
            return 0.65
        if age_days <= 730:
            return 0.45
        return 0.2

    def _parse_date(self, text: str) -> Optional[datetime]:
        raw = str(text or "").strip()
        if not raw:
            return None
        match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", raw)
        if match:
            try:
                return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)
            except ValueError:
                return None
        match = re.search(r"\b(20\d{2})\b", raw)
        if match:
            try:
                return datetime(int(match.group(1)), 1, 1, tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    def _rejection_reason(
        self,
        *,
        row: Dict[str, Any],
        domain: str,
        text: str,
        duplicate: bool,
        extraction_quality: float,
    ) -> Optional[str]:
        if duplicate:
            return "duplicate_content"
        url = str(row.get("link") or row.get("url") or "").strip().lower()
        path = urlparse(url).path.lower() if url else ""
        if any(marker in path for marker in LOW_VALUE_PATH_MARKERS) or "?q=" in url:
            return "index_or_search_page"
        low_text = str(text or "").lower()
        if any(marker in low_text for marker in BLOCKED_TEXT_MARKERS):
            return "blocked_or_login_page"
        if domain in {"quora.com", "reddit.com", "medium.com"} and str(row.get("tier") or "").lower() not in {"official", "trusted"}:
            return "low_authority_page"
        if extraction_quality < 0.25:
            return "thin_page"
        if extraction_quality < 0.38 and not (row.get("published_at") or row.get("date_hint")):
            return "undated_thin_snippet"
        return None

    def _reason(self, *, tier: str, usable: bool, rejection_reason: Optional[str]) -> str:
        if not usable:
            return f"Rejected by source-quality gate: {rejection_reason or 'low_quality'}"
        if tier == "official":
            return "Official or source-of-record evidence accepted"
        if tier == "trusted":
            return "Trusted technical or institutional evidence accepted"
        if tier == "reporting":
            return "Reporting source accepted as secondary evidence"
        return "General source accepted with lower authority weight"

    def _count_rejections(self, rows: List[Dict[str, Any]]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for row in rows:
            reason = str(row.get("rejection_reason") or "").strip()
            if not reason:
                continue
            counts[reason] = counts.get(reason, 0) + 1
        return counts

    def _is_freshness_sensitive(self, query: str) -> bool:
        return bool(
            re.search(
                r"\b(latest|current|today|this week|recent|2026|new|released|changed|pricing|model|version|api)\b",
                str(query or ""),
                flags=re.I,
            )
        )
