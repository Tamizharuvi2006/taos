from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class ResearchEvalCase:
    id: str
    query: str
    expected_route: str
    category: str
    freshness_required: bool
    min_sources: int
    expected_behavior: List[str] = field(default_factory=list)
    forbidden_behavior: List[str] = field(default_factory=list)
    requires_official_source: bool = False
    requires_fresh_sources: bool = False
    requires_source_diversity: bool = False
    requires_conflict_handling: bool = False
    expects_weak_or_no_evidence: bool = False
    must_not_hallucinate: bool = False

    @property
    def case_id(self) -> str:
        return self.id

    @property
    def is_package_version_case(self) -> bool:
        query = self.query.lower()
        if self.expected_route != "fast_search":
            return False
        has_version_intent = any(term in query for term in ("version", "latest", "current", "release"))
        has_package_signal = any(term in query for term in ("vite", "react", "next", "npm", "pypi", "package"))
        return bool(has_version_intent and has_package_signal)

    @classmethod
    def from_dict(cls, row: Dict[str, Any]) -> "ResearchEvalCase":
        category = str(row.get("category") or "").strip()
        expected_route = str(row.get("expected_route") or "").strip()
        if not expected_route:
            if category == "official_source_needed" or bool(row.get("requires_official_source")):
                expected_route = "official_search"
            elif category == "research_comparison":
                expected_route = "comparison_search"
            else:
                expected_route = "news_search"
        expected_freshness = str(row.get("expected_freshness") or "").strip().lower()
        requires_fresh = bool(row.get("requires_fresh_sources") or row.get("freshness_sensitive"))
        return cls(
            id=str(row.get("id") or row.get("case_id") or "").strip(),
            query=str(row.get("query") or "").strip(),
            expected_route=expected_route,
            category=category,
            freshness_required=bool(row.get("freshness_required")) or requires_fresh or expected_freshness in {"high", "medium"},
            min_sources=int(row.get("min_sources") or (2 if bool(row.get("weak_evidence_expected")) else 3)),
            expected_behavior=[
                str(x).strip()
                for x in list(row.get("expected_behavior") or row.get("expected_keywords") or [])
                if str(x).strip()
            ],
            forbidden_behavior=[
                str(x).strip()
                for x in list(row.get("forbidden_behavior") or row.get("forbidden_keywords") or [])
                if str(x).strip()
            ],
            requires_official_source=bool(row.get("requires_official_source")),
            requires_fresh_sources=requires_fresh,
            requires_source_diversity=bool(row.get("requires_source_diversity")),
            requires_conflict_handling=bool(row.get("requires_conflict_handling")),
            expects_weak_or_no_evidence=bool(row.get("expects_weak_or_no_evidence") or row.get("weak_evidence_expected")),
            must_not_hallucinate=bool(row.get("must_not_hallucinate")),
        )


@dataclass
class ResearchEvalResult:
    case_id: str
    query: str
    category: str
    expected_route: str
    route: str
    mode: str
    answer: str
    confidence: float
    sources_count: int
    unique_domains: int
    citation_coverage: float
    supported_claims: int
    unsupported_claims: int
    freshness_score: float
    stale_detected: bool
    extraction_recovery_used: bool
    warnings: List[str]
    latency_ms: float
    route_accuracy: float
    groundedness_score: float
    citation_quality: float
    source_diversity: float
    freshness_correctness: float
    confidence_calibration: float
    hallucination_resistance: float
    latency_score: float
    overall_score: float
    observed_route: str = ""
    route_pass: bool = False
    answer_not_empty: bool = False
    answer_first: bool = False
    generic_failure_detected: bool = False
    answer_mode: str = ""
    usable_sources_count: int = 0
    official_source_count: int = 0
    trusted_source_count: int = 0
    extraction_success_ratio: float = 0.0
    coverage: float = 0.0
    unsupported_critical_claims: int = 0
    freshness_mode: str = ""
    conflict_detected: bool = False
    conflict_summary_present: bool = False
    confidence_calibrated: bool = False
    answer_utility: float = 0.0
    source_quality: float = 0.0
    fallback_usefulness: float = 0.0
    passed: bool = False
    result_band: str = ""
    expected_behavior_hits: List[str] = field(default_factory=list)
    forbidden_behavior_hits: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ResearchEvalHarness:
    def load_cases(self, path: str | Path) -> List[ResearchEvalCase]:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        return [ResearchEvalCase.from_dict(row) for row in rows]

    async def evaluate_cases(
        self,
        *,
        cases: Iterable[ResearchEvalCase],
        runner: Any,
    ) -> Dict[str, Any]:
        results: List[ResearchEvalResult] = []
        for case in cases:
            started = time.perf_counter()
            payload = await self._run_case(case=case, runner=runner)
            latency_ms = float((time.perf_counter() - started) * 1000.0)
            result = self._score_case(case=case, payload=payload, latency_ms=latency_ms)
            results.append(result)
        aggregates = self._aggregate(results)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results": [result.to_dict() for result in results],
            "aggregates": aggregates,
        }

    async def _run_case(self, *, case: ResearchEvalCase, runner: Any) -> Dict[str, Any]:
        if hasattr(runner, "run_case"):
            return dict(await runner.run_case(case))
        if callable(runner):
            return dict(await runner(case))
        raise TypeError("runner must be callable or expose run_case(case)")

    def _score_case(
        self,
        *,
        case: ResearchEvalCase,
        payload: Dict[str, Any],
        latency_ms: float,
    ) -> ResearchEvalResult:
        answer = str(payload.get("answer") or payload.get("formatted_response") or payload.get("result") or "").strip()
        route = str(payload.get("route") or payload.get("route_label") or "").strip()
        mode = str(payload.get("mode") or "").strip()
        sources = list(payload.get("sources") or [])
        trust = dict(payload.get("trust_block") or {})
        metadata = dict(payload.get("metadata") or {})
        trace = dict(payload.get("trace") or metadata.get("trace") or {})
        evidence_stats = dict(
            metadata.get("evidence_stats")
            or trace.get("evidence_stats")
            or trust.get("evidence_stats")
            or {}
        )
        answer_policy = dict(
            evidence_stats.get("answer_policy")
            or trust.get("answer_policy")
            or metadata.get("answer_policy")
            or {}
        )
        evidence = dict(payload.get("evidence_matrix_summary") or trust.get("evidence_matrix_summary") or {})
        freshness_summary = dict(payload.get("freshness_summary") or trust.get("freshness_summary") or metadata.get("freshness_summary") or {})
        warnings = [str(item).strip() for item in list(payload.get("warnings") or []) if str(item).strip()]

        unique_domains = self._count_unique_domains(sources=sources, payload=payload)
        sources_count = len(sources)
        citation_coverage = float(
            evidence.get("citation_coverage")
            or evidence.get("coverage")
            or trust.get("citation_coverage")
            or evidence_stats.get("coverage")
            or 0.0
        )
        if citation_coverage <= 0.0 and sources:
            citation_markers = len(set(re.findall(r"\[S\d+\]", answer, flags=re.I)))
            citation_coverage = min(1.0, citation_markers / float(max(1, min(len(sources), 3))))
        supported_claims = int(evidence.get("supported_claims") or trust.get("supported_claims") or evidence_stats.get("claims_supported") or 0)
        unsupported_claims = int(evidence.get("unsupported_claims") or trust.get("unsupported_claims") or evidence_stats.get("unsupported_claims") or evidence_stats.get("claims_unsupported") or 0)
        unsupported_critical_claims = int(
            evidence.get("unsupported_critical_claims")
            or trust.get("unsupported_critical_claims")
            or evidence_stats.get("unsupported_critical_claims")
            or 0
        )
        freshness_score = float(freshness_summary.get("freshness_score") or 0.0)
        if freshness_score <= 0.0:
            freshness_label = str(trust.get("freshness") or "").strip().lower()
            freshness_score = {"high": 0.9, "medium": 0.65, "low": 0.25}.get(freshness_label, 0.0)
        stale_detected = bool(freshness_summary.get("stale_detected") or trust.get("stale_detected"))
        extraction_recovery_used = bool(
            payload.get("extraction_recovery_used")
            or trust.get("extraction_recovery_used")
            or metadata.get("extraction_recovery_used")
        )
        confidence = float(payload.get("confidence") or trust.get("confidence_score") or 0.0)
        answer_mode = str(
            payload.get("answer_mode")
            or trust.get("answer_mode")
            or evidence_stats.get("answer_mode")
            or answer_policy.get("answer_mode")
            or ""
        ).strip().lower()
        usable_sources_count = int(
            evidence_stats.get("usable_sources_count")
            or evidence_stats.get("usable_source_count")
            or trust.get("usable_sources_count")
            or sources_count
        )
        official_source_count = int(
            evidence_stats.get("official_source_count")
            or trust.get("official_source_count")
            or metadata.get("official_source_count")
            or 0
        )
        trusted_source_count = int(
            evidence_stats.get("trusted_source_count")
            or trust.get("trusted_source_count")
            or metadata.get("trusted_source_count")
            or 0
        )
        extract_attempts = float(evidence_stats.get("extract_attempted_count") or evidence_stats.get("extract_fetch_count") or metadata.get("extract_attempted_count") or 0.0)
        extract_success = float(evidence_stats.get("extract_success_count") or evidence_stats.get("extract_count") or metadata.get("extract_success_count") or 0.0)
        extraction_success_ratio = 1.0 if extract_attempts <= 0 and sources_count > 0 else max(0.0, min(1.0, extract_success / max(1.0, extract_attempts)))
        freshness_mode = str(
            freshness_summary.get("freshness_mode")
            or evidence_stats.get("freshness_mode")
            or ("high" if case.freshness_required else "normal")
        ).strip().lower()
        conflict_summary = dict(
            payload.get("conflict_summary")
            or trust.get("conflict_summary")
            or evidence_stats.get("conflict_summary")
            or {}
        )
        conflict_detected = bool(
            payload.get("conflict_detected")
            or trust.get("conflict_detected")
            or evidence_stats.get("conflict_detected")
            or conflict_summary.get("conflict_detected")
            or conflict_summary.get("groups")
        )
        conflict_summary_present = bool(conflict_summary and (conflict_summary.get("groups") or conflict_summary.get("conflict_detected") is not None))

        expected_hits = [text for text in case.expected_behavior if text.lower() in answer.lower()]
        forbidden_hits = [text for text in case.forbidden_behavior if text.lower() in answer.lower()]

        route_accuracy = 1.0 if route == case.expected_route else 0.0
        route_pass = bool(route_accuracy >= 1.0)
        groundedness_score = max(0.0, min(1.0, citation_coverage + (0.1 if supported_claims > 0 else 0.0) - (unsupported_claims * 0.12)))
        citation_quality = max(0.0, min(1.0, citation_coverage))
        source_diversity = max(0.0, min(1.0, unique_domains / max(1, case.min_sources or 3)))
        source_quality = max(0.0, min(1.0, ((usable_sources_count / max(1, sources_count)) if sources_count else 0.0) * 0.45 + source_diversity * 0.25 + extraction_success_ratio * 0.15 + (1.0 if official_source_count > 0 else 0.0) * 0.15))
        if case.requires_official_source and official_source_count <= 0:
            source_quality = min(source_quality, 0.55)
        if case.requires_source_diversity and source_diversity < 0.6:
            source_quality = min(source_quality, 0.65)
        freshness_correctness = 1.0 if (not case.freshness_required or freshness_score >= 0.5) and not stale_detected else 0.35 if freshness_score > 0 else 0.0
        confidence_calibration = self._score_confidence_calibration(
            confidence=confidence,
            unsupported_claims=unsupported_claims,
            stale_detected=stale_detected,
            extraction_recovery_used=extraction_recovery_used,
        )
        hallucination_resistance = 0.0 if forbidden_hits else 1.0
        if case.category == "failure" and "no reliable evidence found." in " ".join(warnings).lower():
            hallucination_resistance = 1.0
        latency_score = self._score_latency(latency_ms=latency_ms, route=route)
        behavior_score = len(expected_hits) / max(1, len(case.expected_behavior)) if case.expected_behavior else 1.0
        min_source_score = 1.0 if sources_count >= case.min_sources else min(1.0, sources_count / max(1, case.min_sources))
        answer_not_empty = bool(answer)
        answer_first = self._is_answer_first(answer)
        generic_failure_detected = self._has_generic_failure(answer)
        useful_modes = {"verified", "best_supported", "partial_but_useful", "weak_candidate"}
        answer_utility = 0.0
        if answer_not_empty:
            answer_utility = 0.45
            if answer_first:
                answer_utility += 0.25
            if answer_mode in useful_modes:
                answer_utility += 0.2
            if any(term in answer.lower() for term in ("confidence", "because", "source", "uncertain", "treat carefully")):
                answer_utility += 0.1
        if generic_failure_detected and usable_sources_count > 0:
            answer_utility = min(answer_utility, 0.35)
            hallucination_resistance = min(hallucination_resistance, 0.45)
        fallback_usefulness = 1.0
        if usable_sources_count > 0:
            fallback_usefulness = 1.0 if not generic_failure_detected and answer_mode in useful_modes else 0.35
        elif answer_mode == "no_usable_evidence":
            fallback_usefulness = 1.0
        elif generic_failure_detected:
            fallback_usefulness = 0.8
        if case.expects_weak_or_no_evidence and answer_mode in {"weak_candidate", "no_usable_evidence"} and not forbidden_hits:
            fallback_usefulness = max(fallback_usefulness, 0.9)
        if unsupported_critical_claims > 0:
            citation_quality = min(citation_quality, 0.45)
            answer_utility = min(answer_utility, 0.5)
            confidence_calibration = min(confidence_calibration, 0.5)
        if case.category == "failure" and answer_mode == "no_usable_evidence" and sources_count == 0:
            answer_utility = max(answer_utility, 0.8)
            source_quality = max(source_quality, 0.8)
            citation_quality = max(citation_quality, 0.75)
            freshness_correctness = max(freshness_correctness, 0.75)
            fallback_usefulness = 1.0
        conflict_score = 1.0
        if case.requires_conflict_handling:
            conflict_score = 1.0 if conflict_summary_present else 0.45
        elif conflict_detected:
            conflict_score = 1.0 if conflict_summary_present else 0.5

        overall = (
            route_accuracy * 0.15
            + answer_utility * 0.2
            + source_quality * 0.2
            + citation_quality * 0.15
            + freshness_correctness * 0.1
            + fallback_usefulness * 0.1
            + latency_score * 0.05
            + conflict_score * 0.05
        )
        overall = min(overall, overall * (0.85 + hallucination_resistance * 0.15))
        overall = min(1.0, overall * (0.95 + min_source_score * 0.05) * (0.95 + behavior_score * 0.05))
        result_band = self._result_band(overall)
        passed = overall >= 0.75 or (case.expects_weak_or_no_evidence and overall >= 0.65 and hallucination_resistance >= 0.8)

        return ResearchEvalResult(
            case_id=case.id,
            query=case.query,
            category=case.category,
            expected_route=case.expected_route,
            route=route,
            mode=mode,
            answer=answer,
            confidence=round(confidence, 3),
            sources_count=sources_count,
            unique_domains=unique_domains,
            citation_coverage=round(citation_coverage, 3),
            supported_claims=supported_claims,
            unsupported_claims=unsupported_claims,
            freshness_score=round(freshness_score, 3),
            stale_detected=stale_detected,
            extraction_recovery_used=extraction_recovery_used,
            warnings=warnings,
            latency_ms=round(latency_ms, 2),
            route_accuracy=round(route_accuracy, 3),
            groundedness_score=round(groundedness_score, 3),
            citation_quality=round(citation_quality, 3),
            source_diversity=round(source_diversity, 3),
            freshness_correctness=round(freshness_correctness, 3),
            confidence_calibration=round(confidence_calibration, 3),
            hallucination_resistance=round(hallucination_resistance, 3),
            latency_score=round(latency_score, 3),
            overall_score=round(max(0.0, min(1.0, overall)), 3),
            observed_route=route,
            route_pass=route_pass,
            answer_not_empty=answer_not_empty,
            answer_first=answer_first,
            generic_failure_detected=generic_failure_detected,
            answer_mode=answer_mode,
            usable_sources_count=usable_sources_count,
            official_source_count=official_source_count,
            trusted_source_count=trusted_source_count,
            extraction_success_ratio=round(extraction_success_ratio, 3),
            coverage=round(citation_coverage, 3),
            unsupported_critical_claims=unsupported_critical_claims,
            freshness_mode=freshness_mode,
            conflict_detected=conflict_detected,
            conflict_summary_present=conflict_summary_present,
            confidence_calibrated=confidence_calibration >= 0.6,
            answer_utility=round(answer_utility, 3),
            source_quality=round(source_quality, 3),
            fallback_usefulness=round(fallback_usefulness, 3),
            passed=passed,
            result_band=result_band,
            expected_behavior_hits=expected_hits,
            forbidden_behavior_hits=forbidden_hits,
        )

    def _aggregate(self, results: List[ResearchEvalResult]) -> Dict[str, Any]:
        if not results:
            return {
                "route_accuracy": 0.0,
                "groundedness_score": 0.0,
                "citation_quality": 0.0,
                "source_diversity": 0.0,
                "freshness_score": 0.0,
                "confidence_calibration": 0.0,
                "hallucination_resistance": 0.0,
                "answer_utility": 0.0,
                "source_quality": 0.0,
                "fallback_usefulness": 0.0,
                "latency_score": 0.0,
                "overall_score": 0.0,
                "pass_rate": 0.0,
                "cases_run": 0,
                "failed_cases": [],
            }
        def avg(field: str) -> float:
            return round(sum(float(getattr(row, field) or 0.0) for row in results) / len(results), 3)

        failed_cases = [row.case_id for row in results if not row.passed]
        weak_areas = self._weak_areas({
            "route_accuracy": avg("route_accuracy"),
            "answer_utility": avg("answer_utility"),
            "source_quality": avg("source_quality"),
            "groundedness_score": avg("groundedness_score"),
            "citation_quality": avg("citation_quality"),
            "source_diversity": avg("source_diversity"),
            "freshness_score": avg("freshness_correctness"),
            "confidence_calibration": avg("confidence_calibration"),
            "hallucination_resistance": avg("hallucination_resistance"),
            "fallback_usefulness": avg("fallback_usefulness"),
            "latency_score": avg("latency_score"),
        })
        return {
            "route_accuracy": avg("route_accuracy"),
            "answer_utility": avg("answer_utility"),
            "source_quality": avg("source_quality"),
            "groundedness_score": avg("groundedness_score"),
            "citation_quality": avg("citation_quality"),
            "source_diversity": avg("source_diversity"),
            "freshness_score": avg("freshness_correctness"),
            "confidence_calibration": avg("confidence_calibration"),
            "hallucination_resistance": avg("hallucination_resistance"),
            "fallback_usefulness": avg("fallback_usefulness"),
            "latency_score": avg("latency_score"),
            "overall_score": avg("overall_score"),
            "pass_rate": round(sum(1 for row in results if row.passed) / max(1, len(results)), 3),
            "cases_run": len(results),
            "failed_cases": failed_cases,
            "weak_areas": weak_areas,
            "recommendations": self._recommended_fixes(weak_areas),
        }

    def render_markdown_report(self, evaluation: Dict[str, Any]) -> str:
        timestamp = str(evaluation.get("timestamp") or "")
        aggregates = dict(evaluation.get("aggregates") or {})
        results = list(evaluation.get("results") or [])
        failed = [row for row in results if not bool(row.get("passed"))]
        weak_areas = list(aggregates.get("weak_areas") or self._weak_areas(aggregates))
        lines = [
            "# Research Evaluation Report",
            "",
            f"Timestamp: {timestamp}",
            "",
            "## Summary",
            "",
            "| Metric | Score |",
            "| --- | ---: |",
            f"| Route Accuracy | {aggregates.get('route_accuracy', 0.0):.3f} |",
            f"| Answer Utility | {aggregates.get('answer_utility', 0.0):.3f} |",
            f"| Source Quality | {aggregates.get('source_quality', 0.0):.3f} |",
            f"| Groundedness | {aggregates.get('groundedness_score', 0.0):.3f} |",
            f"| Citation Quality | {aggregates.get('citation_quality', 0.0):.3f} |",
            f"| Source Diversity | {aggregates.get('source_diversity', 0.0):.3f} |",
            f"| Freshness Score | {aggregates.get('freshness_score', 0.0):.3f} |",
            f"| Confidence Calibration | {aggregates.get('confidence_calibration', 0.0):.3f} |",
            f"| Hallucination Resistance | {aggregates.get('hallucination_resistance', 0.0):.3f} |",
            f"| Fallback Usefulness | {aggregates.get('fallback_usefulness', 0.0):.3f} |",
            f"| Latency Score | {aggregates.get('latency_score', 0.0):.3f} |",
            f"| Overall Score | {aggregates.get('overall_score', 0.0):.3f} |",
            f"| Pass Rate | {aggregates.get('pass_rate', 0.0):.3f} |",
            "",
            "## Failed Cases",
            "",
        ]
        if not failed:
            lines.append("- None")
        else:
            for row in failed:
                lines.append(
                    f"- `{row['case_id']}` route={row['route']} mode={row.get('answer_mode', '')} overall={row['overall_score']:.3f} unsupported={row['unsupported_claims']} latency_ms={row['latency_ms']}"
                )
        lines.extend(["", "## Weak Areas", ""])
        if not weak_areas:
            lines.append("- None")
        else:
            for row in weak_areas:
                lines.append(f"- {row}")
        lines.extend(["", "## Recommended Fixes", ""])
        fixes = list(aggregates.get("recommendations") or self._recommended_fixes(weak_areas))
        if not fixes:
            lines.append("- Maintain current benchmark and expand case coverage.")
        else:
            for row in fixes:
                lines.append(f"- {row}")
        return "\n".join(lines).strip() + "\n"

    def _weak_areas(self, aggregates: Dict[str, Any]) -> List[str]:
        areas = []
        labels = {
            "route_accuracy": "Route accuracy is below target.",
            "answer_utility": "Answer utility is below target.",
            "source_quality": "Source quality is weak.",
            "groundedness_score": "Groundedness is weak.",
            "citation_quality": "Citation quality is weak.",
            "source_diversity": "Source diversity is weak.",
            "freshness_score": "Freshness handling is weak.",
            "confidence_calibration": "Confidence calibration is weak.",
            "hallucination_resistance": "Hallucination resistance is weak.",
            "fallback_usefulness": "Fallback usefulness is weak.",
            "latency_score": "Latency is weak.",
        }
        for key, label in labels.items():
            if float(aggregates.get(key) or 0.0) < 0.75:
                areas.append(label)
        return areas

    def _recommended_fixes(self, weak_areas: List[str]) -> List[str]:
        recommendations = []
        for area in weak_areas:
            lower = area.lower()
            if "route" in lower:
                recommendations.append("Expand search-depth router eval cases and add more route guard heuristics.")
            elif "answer utility" in lower:
                recommendations.append("Strengthen answer-mode finalization so useful evidence answers before warnings.")
            elif "source quality" in lower:
                recommendations.append("Increase official/trusted source preference and extraction quality gating.")
            elif "groundedness" in lower or "citation" in lower:
                recommendations.append("Tighten evidence-matrix claim filtering and citation density rules.")
            elif "diversity" in lower:
                recommendations.append("Increase domain diversity pressure and official-source query coverage.")
            elif "freshness" in lower:
                recommendations.append("Refine freshness policy windows and stale-source penalties.")
            elif "confidence" in lower:
                recommendations.append("Increase calibration penalties when unsupported claims or recovery paths are present.")
            elif "hallucination" in lower:
                recommendations.append("Add more no-result and contradiction cases to force explicit uncertainty.")
            elif "fallback" in lower:
                recommendations.append("Penalize generic no-answer fallbacks when usable evidence exists.")
            elif "latency" in lower:
                recommendations.append("Add cache coverage and reduce accidental deep-search routing for simple current lookups.")
        deduped = []
        seen = set()
        for row in recommendations:
            if row in seen:
                continue
            seen.add(row)
            deduped.append(row)
        return deduped

    def _is_answer_first(self, answer: str) -> bool:
        text = str(answer or "").strip().lower()
        if not text:
            return False
        starters = (
            "answer",
            "the best-supported answer",
            "the best supported answer",
            "based on the strongest",
            "based on the best",
            "the likely answer",
        )
        return text.startswith(starters)

    def _has_generic_failure(self, answer: str) -> bool:
        text = str(answer or "").strip().lower()
        if not text:
            return True
        generic = (
            "i couldn't verify",
            "i could not verify",
            "could not verify this",
            "couldn't verify this",
            "unable to verify",
            "no reliable evidence",
            "i could not find reliable",
        )
        return any(term in text for term in generic)

    def _result_band(self, score: float) -> str:
        value = float(score or 0.0)
        if value >= 0.85:
            return "strong"
        if value >= 0.75:
            return "acceptable"
        if value >= 0.65:
            return "weak_but_usable"
        return "fail"

    def _count_unique_domains(self, *, sources: List[Any], payload: Dict[str, Any]) -> int:
        domains = set()
        for item in sources:
            if isinstance(item, dict):
                value = str(item.get("link") or item.get("url") or item.get("source") or "").strip()
            else:
                value = str(item or "").strip()
            if not value:
                continue
            domain = value.split("/")[2] if "://" in value and len(value.split("/")) > 2 else value
            domain = domain.replace("www.", "")
            domains.add(domain)
        if not domains:
            trust = dict(payload.get("trust_block") or {})
            diversity = float(trust.get("source_diversity_score") or trust.get("domain_diversity") or 0.0)
            source_count = len(sources)
            if diversity > 0 and source_count > 0:
                return max(1, round(diversity * source_count))
        return len(domains)

    def _score_confidence_calibration(
        self,
        *,
        confidence: float,
        unsupported_claims: int,
        stale_detected: bool,
        extraction_recovery_used: bool,
    ) -> float:
        if unsupported_claims <= 0 and not stale_detected and not extraction_recovery_used:
            return 1.0 if confidence >= 0.5 else 0.7
        if confidence <= 0.55:
            return 1.0
        if confidence <= 0.7:
            return 0.6
        return 0.2

    def _score_latency(self, *, latency_ms: float, route: str) -> float:
        route_norm = str(route or "").strip().lower()
        ideal = 2500.0 if route_norm in {"fast_search", "news_search"} else 9000.0
        if latency_ms <= ideal:
            return 1.0
        if latency_ms >= ideal * 3:
            return 0.1
        return round(max(0.1, 1.0 - ((latency_ms - ideal) / (ideal * 2))), 3)


def load_research_eval_cases(path: str | Path) -> List[ResearchEvalCase]:
    return ResearchEvalHarness().load_cases(path)


def score_research_response(case: ResearchEvalCase, response: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(response or {})
    if not payload.get("route") and not payload.get("route_label"):
        payload["route"] = case.expected_route
    result = ResearchEvalHarness()._score_case(case=case, payload=payload, latency_ms=0.0)
    answer = str(payload.get("answer") or payload.get("formatted_response") or payload.get("result") or "").lower()
    uncertainty_terms = ("uncertain", "unclear", "not confirmed", "unverified", "could not verify", "no reliable")
    overclaim_terms = ("confirmed and final", "no uncertainty", "definitely", "confirmed culprit")
    weak_expected = case.category in {"weak_evidence", "conflicting_news"} or bool(result.unsupported_claims)
    uncertainty_honesty = 1.0
    if weak_expected:
        if any(term in answer for term in overclaim_terms):
            uncertainty_honesty = 0.25
        else:
            uncertainty_honesty = 0.9 if any(term in answer for term in uncertainty_terms) else 0.25
    scores = {
        "route_accuracy": result.route_accuracy,
        "groundedness": result.groundedness_score,
        "citation_usefulness": result.citation_quality,
        "source_diversity": result.source_diversity,
        "freshness": result.freshness_correctness,
        "confidence_calibration": result.confidence_calibration,
        "hallucination_resistance": result.hallucination_resistance,
        "latency": result.latency_score,
        "uncertainty_honesty": uncertainty_honesty,
    }
    overall = round((result.overall_score * 0.85) + (uncertainty_honesty * 0.15), 3)
    return {
        "case_id": result.case_id,
        "query": result.query,
        "scores": scores,
        "overall_score": max(0.0, min(1.0, overall)),
        "summary": "good" if overall >= 0.7 else "needs_tuning",
    }
