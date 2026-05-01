from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence
from urllib.parse import urlparse

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_PARENT = _REPO_ROOT.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))

from taos.core.entity import (  # noqa: E402
    EntityAnswerComposer,
    EntityEvidence,
    EntityIntent,
    EntityIntentDetector,
    EntityProfileCandidate,
    EntitySourcePlanner,
)
from taos.core.research import ResearchPipeline  # noqa: E402
from taos.scripts.run_provider_connectivity_check import collect_provider_connectivity  # noqa: E402


DEFAULT_CASES = _REPO_ROOT / "qa" / "live_evidence_cases.json"
DEFAULT_JSON = _REPO_ROOT / "QA_RESULTS_LIVE_EVIDENCE.json"
DEFAULT_MD = _REPO_ROOT / "QA_RESULTS_LIVE_EVIDENCE.md"


@dataclass(frozen=True)
class LiveEvidenceCase:
    id: str
    query: str
    category: str
    expected_route: str
    expected_owner: str
    requested_role: str = ""
    expect_not_verified: bool = False
    expect_exact_role_verified: bool = False
    forbid_candidate_name: str = ""
    require_search_lanes: tuple[str, ...] = ()
    require_research_lanes: tuple[str, ...] = ()
    allow_supported_roles: tuple[str, ...] = ()
    require_official_source: bool = False
    require_freshness: bool = False
    expect_related_evidence: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "LiveEvidenceCase":
        return cls(
            id=str(row.get("id") or "").strip(),
            query=str(row.get("query") or "").strip(),
            category=str(row.get("category") or "").strip(),
            expected_route=str(row.get("expected_route") or "").strip(),
            expected_owner=str(row.get("expected_owner") or "").strip(),
            requested_role=str(row.get("requested_role") or "").strip().lower(),
            expect_not_verified=bool(row.get("expect_not_verified")),
            expect_exact_role_verified=bool(row.get("expect_exact_role_verified")),
            forbid_candidate_name=str(row.get("forbid_candidate_name") or "").strip(),
            require_search_lanes=tuple(str(item) for item in row.get("require_search_lanes") or ()),
            require_research_lanes=tuple(str(item) for item in row.get("require_research_lanes") or ()),
            allow_supported_roles=tuple(str(item).strip().lower() for item in row.get("allow_supported_roles") or ()),
            require_official_source=bool(row.get("require_official_source")),
            require_freshness=bool(row.get("require_freshness")),
            expect_related_evidence=bool(row.get("expect_related_evidence")),
            raw=dict(row),
        )


def load_cases(path: str | Path = DEFAULT_CASES, *, max_cases: int | None = None) -> List[LiveEvidenceCase]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [LiveEvidenceCase.from_dict(row) for row in rows if isinstance(row, Mapping)]
    return cases[:max_cases] if max_cases is not None and max_cases >= 0 else cases


def run_cases(
    cases: Sequence[LiveEvidenceCase],
    *,
    live: bool = False,
    base_url: str = "http://127.0.0.1:8000",
    auth_token: str = "",
    timeout: float = 60.0,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    blockers: List[str] = []
    provider_diagnostics: Dict[str, Any] = {}
    if live:
        diagnostics_value = collect_provider_connectivity(base_url=base_url, timeout=min(timeout, 20.0))
        provider_diagnostics = (
            asyncio.run(diagnostics_value) if inspect.isawaitable(diagnostics_value) else dict(diagnostics_value or {})
        )
    for case in cases:
        started = time.perf_counter()
        payload = _execute_live(case, base_url=base_url, auth_token=auth_token, timeout=timeout) if live else _execute_mock(case)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
        result = validate_case(
            case,
            payload=payload,
            elapsed_ms=elapsed_ms,
            live=live,
            provider_diagnostics=provider_diagnostics,
        )
        results.append(result)
        if result.get("live_blocker"):
            blockers.append(str(result["live_blocker"]))
    passed = sum(1 for row in results if row["ok"])
    failed = len(results) - passed
    return {
        "phase": "147",
        "mode": "live" if live else "mock",
        "base_url": base_url if live else "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / max(1, len(results)),
        "live_blockers": sorted(dict.fromkeys(blockers)),
        "provider_diagnostics": provider_diagnostics,
        "results": results,
    }


def validate_case(
    case: LiveEvidenceCase,
    *,
    payload: Dict[str, Any],
    elapsed_ms: float,
    live: bool,
    provider_diagnostics: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    fields = extract_report_fields(payload)
    if provider_diagnostics:
        fields["provider_diagnostics"] = dict(provider_diagnostics)
    checks: Dict[str, bool] = {
        "answer_not_empty": bool(fields["final_answer"].strip()),
        "route_matches": fields["selected_route"] == case.expected_route,
        "owner_matches": fields["owner"] == case.expected_owner,
        "evidence_matrix_present": bool(fields["evidence_matrix"]),
        "trust_metadata_present": bool(fields["trust_metadata"]),
        "no_overclaim": not _answer_overclaims(fields),
        "source_tiers_present": bool(fields["source_tiers_found"]),
    }
    if case.require_search_lanes:
        for lane in case.require_search_lanes:
            checks[f"lane_{lane}_present"] = lane in fields["search_lanes_used"]
    if case.require_research_lanes:
        for lane in case.require_research_lanes:
            checks[f"research_lane_{lane}_present"] = lane in fields["search_lanes_used"]
    if case.requested_role:
        checks["requested_role_present"] = fields["trust_metadata"].get("requested_role") == case.requested_role
    if case.expect_not_verified:
        checks["not_verified"] = not bool(fields["exact_claim_verified"])
    if case.expect_exact_role_verified:
        checks["exact_role_verified"] = bool(fields["exact_claim_verified"])
    if case.forbid_candidate_name:
        checks["forbid_wrong_candidate"] = not (
            bool(fields["exact_claim_verified"])
            and str(fields["trust_metadata"].get("selected_candidate") or "").strip().lower() == case.forbid_candidate_name.lower()
        )
    if case.allow_supported_roles:
        supported = str(fields["trust_metadata"].get("supported_role") or "").strip().lower()
        checks["supported_role_allowed"] = supported in set(case.allow_supported_roles)
    if case.require_official_source:
        checks["official_source_present"] = bool(fields["trust_metadata"].get("official_source_found"))
    if case.require_freshness:
        checks["freshness_present"] = bool(fields["trust_metadata"].get("freshness_status"))
    if case.expect_related_evidence:
        checks["related_evidence_present"] = bool(fields["trust_metadata"].get("related_evidence_used"))

    failed_checks = [name for name, ok in checks.items() if not ok]
    live_blocker = ""
    if live and payload.get("_http_error"):
        live_blocker = str(payload.get("_http_error") or "")
    elif live and int(payload.get("_http_status") or 0) in {401, 403, 500, 0}:
        live_blocker = f"http_status_{payload.get('_http_status')}"

    return {
        "id": case.id,
        "query": case.query,
        "category": case.category,
        "mode": "live" if live else "mock",
        "ok": not failed_checks,
        "failed_checks": failed_checks,
        "elapsed_ms": elapsed_ms,
        "selected_route": fields["selected_route"],
        "owner": fields["owner"],
        "answer_mode": fields["answer_mode"],
        "final_answer": fields["final_answer"],
        "exact_claim_verified": bool(fields["exact_claim_verified"]),
        "role_match": fields["role_match"],
        "source_tiers_found": fields["source_tiers_found"],
        "search_queries_generated": fields["search_queries_generated"],
        "search_lanes_used": fields["search_lanes_used"],
        "provider_returned_count": fields["provider_returned_count"],
        "domains_returned": fields["domains_returned"],
        "returned_titles": fields["returned_titles"],
        "returned_snippets": fields["returned_snippets"],
        "extracted_urls_attempted": fields["extracted_urls_attempted"],
        "extraction_statuses": fields["extraction_statuses"],
        "rejected_sources": fields["rejected_sources"],
        "rejected_sources_count": fields["rejected_sources_count"],
        "usable_sources_count": fields["usable_sources_count"],
        "conflict_detected": fields["conflict_detected"],
        "confidence_reason": fields["confidence_reason"],
        "evidence_matrix": fields["evidence_matrix"],
        "trust_metadata": fields["trust_metadata"],
        "provider_diagnostics": dict(provider_diagnostics or {}),
        "pass_reason": _pass_reason(case, fields, failed_checks),
        "http_status": payload.get("_http_status", ""),
        "http_error": payload.get("_http_error", ""),
        "live_blocker": live_blocker,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Phase 147 Live Provider Evidence QA",
        "",
        f"- Generated: {report['timestamp']}",
        f"- Mode: {report['mode']}",
        f"- Total: {report['total']}",
        f"- Passed: {report['passed']}",
        f"- Failed: {report['failed']}",
        f"- Pass rate: {report['pass_rate']:.3f}",
    ]
    blockers = list(report.get("live_blockers") or [])
    if blockers:
        lines.extend(["", "## Live blockers"])
        lines.extend(f"- {item}" for item in blockers)
    provider_diagnostics = dict(report.get("provider_diagnostics") or {})
    if provider_diagnostics:
        lines.extend(
            [
                "",
                "## Provider diagnostics",
                f"- search_provider_ready: `{provider_diagnostics.get('search_provider_ready')}`",
                f"- search_provider_name: `{provider_diagnostics.get('search_provider_name') or '-'}`",
                f"- extract_provider_ready: `{provider_diagnostics.get('extract_provider_ready')}`",
                f"- external_network_ready: `{provider_diagnostics.get('external_network_ready')}`",
                f"- provider_mode: `{provider_diagnostics.get('provider_mode') or '-'}`",
                f"- last_provider_error_safe: `{provider_diagnostics.get('last_provider_error_safe') or '-'}`",
            ]
        )
    lines.extend(
        [
            "",
            "| Case | Route | Owner | Answer mode | Exact verified | OK |",
            "| --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for row in report["results"]:
        lines.append(
            f"| `{row['id']}` | {row.get('selected_route') or '-'} | {row.get('owner') or '-'} | "
            f"{row.get('answer_mode') or '-'} | {'yes' if row.get('exact_claim_verified') else 'no'} | "
            f"{'yes' if row.get('ok') else 'no'} |"
        )
    lines.extend(["", "## Detailed results"])
    for row in report["results"]:
        lines.extend(
            [
                "",
                f"### {row['id']}",
                f"- Query: `{row['query']}`",
                f"- Route: `{row.get('selected_route')}`",
                f"- Owner: `{row.get('owner')}`",
                f"- Answer mode: `{row.get('answer_mode')}`",
                f"- Exact claim verified: `{row.get('exact_claim_verified')}`",
                f"- Role match: `{row.get('role_match')}`",
                f"- Source tiers found: `{', '.join(row.get('source_tiers_found') or []) or '-'}`",
                f"- Domains returned: `{', '.join(row.get('domains_returned') or []) or '-'}`",
                f"- Failed checks: `{', '.join(row.get('failed_checks') or []) or '-'}`",
                f"- Pass/fail reason: {row.get('pass_reason') or '-'}",
                "",
                "Answer preview:",
                "```txt",
                str(row.get("final_answer") or "")[:1200],
                "```",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def write_report(report: Dict[str, Any], *, out_json: str | Path = DEFAULT_JSON, out_md: str | Path = DEFAULT_MD) -> None:
    Path(out_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    Path(out_md).write_text(render_markdown(report), encoding="utf-8")


def extract_report_fields(payload: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = _dict(payload.get("metadata"))
    trace = _dict(payload.get("trace"))
    trust = _dict(payload.get("trust_block"))
    evidence_stats = _dict(metadata.get("evidence_stats") or trace.get("evidence_stats"))
    entity_summary = _dict(
        payload.get("entity_intelligence_summary")
        or metadata.get("entity_intelligence_summary")
        or trace.get("entity_intelligence_summary")
    )
    route_boundary = _dict(payload.get("route_boundary_summary") or metadata.get("route_boundary_summary") or trace.get("route_boundary_summary"))
    query_plan_summary = _dict(
        evidence_stats.get("query_plan_summary")
        or trace.get("query_plan_summary")
        or metadata.get("query_plan_summary")
    )
    evidence_matrix_summary = _dict(payload.get("evidence_matrix_summary") or trust.get("evidence_matrix_summary"))
    extractor_candidates = list(trace.get("extractor_candidates") or metadata.get("extractor_candidates") or [])
    raw_search_results = list(trace.get("entity_search_results") or metadata.get("entity_search_results") or [])
    selected_route = str(
        payload.get("selected_route")
        or payload.get("public_route_label")
        or payload.get("route")
        or metadata.get("selected_route")
        or trace.get("selected_route")
        or ""
    ).strip()
    owner = str(
        payload.get("route_owner")
        or payload.get("owner")
        or metadata.get("route_owner")
        or route_boundary.get("owner")
        or ""
    ).strip()
    answer_mode_value = str(
        payload.get("answer_mode")
        or entity_summary.get("answer_mode")
        or evidence_stats.get("entity_answer_mode")
        or evidence_stats.get("answer_mode")
        or trust.get("answer_mode")
        or ""
    ).strip()
    search_lanes_used = list(
        entity_summary.get("search_lanes_used")
        or evidence_stats.get("search_lanes_used")
        or query_plan_summary.get("lanes_used")
        or []
    )
    search_queries_generated = list(query_plan_summary.get("primary_queries") or []) + list(query_plan_summary.get("fallback_queries") or [])
    if not search_queries_generated:
        source_plan = _dict(entity_summary.get("source_plan"))
        lanes = _dict(source_plan.get("lanes"))
        for lane_queries in lanes.values():
            for query in list(lane_queries or []):
                text = str(query or "").strip()
                if text:
                    search_queries_generated.append(text)
    if not search_queries_generated:
        for row in raw_search_results:
            text = str(row.get("query") or "").strip()
            if text:
                search_queries_generated.append(text)
    if not search_queries_generated:
        answer_text = str(payload.get("answer") or payload.get("response") or "")
        if "Sources checked:" in answer_text:
            tail = answer_text.split("Sources checked:", 1)[1]
            for line in tail.splitlines():
                stripped = line.strip()
                if stripped.startswith("Verification policy:"):
                    break
                if stripped.startswith("- "):
                    query_text = stripped[2:].strip()
                    if query_text:
                        search_queries_generated.append(query_text)
    source_tiers_found = list(
        entity_summary.get("source_tiers_found")
        or evidence_stats.get("source_tiers_found")
        or _summary_source_tiers(extractor_candidates)
    )
    exact_claim_verified = (
        entity_summary.get("exact_role_verified")
        if "exact_role_verified" in entity_summary
        else evidence_stats.get("exact_role_verified")
    )
    if exact_claim_verified is None:
        exact_claim_verified = trust.get("exact_claim_confirmed")
    exact_claim_verified = bool(exact_claim_verified)
    role_match = entity_summary.get("role_match")
    if role_match is None:
        role_match = evidence_stats.get("role_match")
    if role_match is None:
        role_match = False
    conflict_detected = bool(
        entity_summary.get("conflict_detected")
        if "conflict_detected" in entity_summary
        else evidence_stats.get("conflict_detected")
        if "conflict_detected" in evidence_stats
        else trust.get("conflict_detected")
    )
    confidence_reason = str(
        entity_summary.get("confidence_reason")
        or evidence_stats.get("confidence_reason")
        or trust.get("confidence_reason")
        or metadata.get("confidence_reason")
        or ""
    ).strip()
    usable_sources_count = int(
        _number(
            entity_summary.get("candidate_count")
            if entity_summary.get("verification_state")
            else trust.get("usable_sources_count")
            or evidence_matrix_summary.get("supported_claims")
            or 0
        )
    )
    rejected_sources_count = int(
        _number(
            trust.get("rejected_sources_count")
            or evidence_stats.get("extract_rejected_count")
            or len([row for row in extractor_candidates if str(row.get("rejection_reason") or "").strip()])
            or 0
        )
    )
    trust_metadata = {
        "verification_state": entity_summary.get("verification_state") or evidence_stats.get("verification_state") or "",
        "requested_role": entity_summary.get("requested_role") or evidence_stats.get("requested_role") or "",
        "supported_role": entity_summary.get("supported_role") or evidence_stats.get("supported_role") or "",
        "role_match": bool(role_match),
        "role_mismatch_reason": entity_summary.get("role_mismatch_reason") or evidence_stats.get("role_mismatch_reason") or "",
        "conflict_detected": conflict_detected,
        "exact_role_verified": exact_claim_verified,
        "evidence_strength": entity_summary.get("evidence_strength") or evidence_stats.get("evidence_strength") or "",
        "strongest_source_type": entity_summary.get("strongest_source_type") or evidence_stats.get("strongest_source_type") or "",
        "official_source_found": bool(
            entity_summary.get("official_source_found")
            or evidence_stats.get("official_source_found")
            or trust.get("official_source_found")
        ),
        "linkedin_source_found": bool(
            entity_summary.get("linkedin_source_found")
            or evidence_stats.get("linkedin_source_found")
            or trust.get("linkedin_source_found")
        ),
        "registry_source_found": bool(
            entity_summary.get("registry_source_found")
            or evidence_stats.get("registry_source_found")
            or trust.get("registry_source_found")
        ),
        "selected_candidate": entity_summary.get("selected_candidate") or evidence_stats.get("selected_candidate") or "",
        "candidate_count": int(_number(entity_summary.get("candidate_count") or evidence_stats.get("candidate_count") or 0)),
        "search_depth_used": entity_summary.get("search_depth_used") or evidence_stats.get("search_depth_used") or "",
        "search_lanes_used": list(search_lanes_used),
        "source_tiers_found": list(source_tiers_found),
        "answer_mode": answer_mode_value,
        "confidence_reason": confidence_reason,
        "freshness_status": trust.get("freshness") or _dict(payload.get("freshness_summary")).get("freshness_mode") or "",
        "related_evidence_used": bool(trust.get("related_evidence_used") or evidence_stats.get("related_evidence_used")),
    }
    provider_diagnostics = _build_provider_diagnostics(payload=payload)
    evidence_matrix = _build_evidence_matrix(
        payload=payload,
        extractor_candidates=extractor_candidates,
        raw_search_results=raw_search_results,
        trust_metadata=trust_metadata,
        source_tiers_found=source_tiers_found,
    )
    domains_returned = sorted(
        {
            row.get("domain")
            for row in evidence_matrix
            if row.get("domain")
        }
        | {
            urlparse(str(row.get("url") or "")).netloc.replace("www.", "")
            for row in raw_search_results
            if str(row.get("url") or "").strip()
        }
    )
    returned_titles = [str(row.get("title") or "").strip() for row in raw_search_results if str(row.get("title") or "").strip()]
    returned_snippets = [str(row.get("snippet") or "").strip()[:240] for row in raw_search_results if str(row.get("snippet") or "").strip()]
    extracted_urls_attempted = [
        str(row.get("url") or "").strip()
        for row in extractor_candidates
        if bool(row.get("attempted")) and str(row.get("url") or "").strip()
    ]
    extraction_statuses = sorted(
        {
            str(row.get("extraction_status") or "").strip()
            for row in extractor_candidates
            if str(row.get("extraction_status") or "").strip()
        }
    )
    rejected_sources = [
        {
            "title": str(row.get("title") or "").strip(),
            "url": str(row.get("url") or "").strip(),
            "rejection_reason": str(row.get("rejection_reason") or "").strip(),
        }
        for row in extractor_candidates
        if str(row.get("rejection_reason") or "").strip()
    ]
    return {
        "selected_route": selected_route,
        "owner": owner,
        "answer_mode": answer_mode_value,
        "final_answer": str(payload.get("answer") or payload.get("response") or "").strip(),
        "exact_claim_verified": exact_claim_verified,
        "role_match": bool(role_match),
        "search_queries_generated": [str(item).strip() for item in search_queries_generated if str(item).strip()],
        "search_lanes_used": [str(item).strip() for item in search_lanes_used if str(item).strip()],
        "provider_returned_count": len(raw_search_results),
        "source_tiers_found": [str(item).strip() for item in source_tiers_found if str(item).strip()],
        "rejected_sources_count": rejected_sources_count,
        "usable_sources_count": usable_sources_count,
        "conflict_detected": conflict_detected,
        "confidence_reason": confidence_reason,
        "evidence_matrix": evidence_matrix,
        "domains_returned": domains_returned,
        "returned_titles": returned_titles,
        "returned_snippets": returned_snippets,
        "extracted_urls_attempted": extracted_urls_attempted,
        "extraction_statuses": extraction_statuses,
        "rejected_sources": rejected_sources,
        "trust_metadata": trust_metadata,
        "provider_diagnostics": provider_diagnostics,
    }


def _execute_mock(case: LiveEvidenceCase) -> Dict[str, Any]:
    if case.category.startswith("entity") or case.category == "legitimacy":
        return _execute_mock_entity(case)
    return _execute_mock_research(case)


def _execute_mock_entity(case: LiveEvidenceCase) -> Dict[str, Any]:
    if case.id == "relyce_official_site":
        summary = {
            "answer_mode": "profile_likely_official",
            "verification_state": "candidate",
            "selected_candidate": "relyce.ai",
            "candidate_count": 1,
            "official_source_found": True,
            "linkedin_source_found": False,
            "registry_source_found": False,
            "requested_role": "",
            "supported_role": "",
            "role_match": False,
            "role_mismatch_reason": "",
            "conflict_detected": False,
            "exact_role_verified": False,
            "evidence_strength": "candidate",
            "strongest_source_type": "official_website",
            "search_depth_used": "standard",
            "search_lanes_used": ["official_website", "general_web"],
            "source_tiers_found": ["tier1"],
            "confidence_reason": "The official-site candidate was linked from strong public signals.",
        }
        return {
            "answer": "The likely official website is relyce.ai.",
            "route": case.expected_route,
            "selected_route": case.expected_route,
            "public_route_label": case.expected_route,
            "route_owner": case.expected_owner,
            "sources": ["https://relyce.ai"],
            "metadata": {"route_owner": case.expected_owner, "entity_intelligence_summary": summary, "evidence_stats": summary},
            "trust_block": {"answer_mode": "profile_likely_official", "confidence_reason": summary["confidence_reason"], "official_source_found": True},
            "trace": {
                "entity_intelligence_summary": summary,
                "evidence_stats": summary,
                "extractor_candidates": [
                    {"title": "Relyce AI", "link": "https://relyce.ai", "provider": "official_website", "page_class": "official_website", "quality_score": 0.9, "rejection_reason": ""}
                ],
            },
            "evidence_matrix_summary": {"supported_claims": 1, "unsupported_critical_claims": 0},
        }
    if case.id == "relyce_legitimacy":
        summary = {
            "answer_mode": "some_public_presence",
            "verification_state": "candidate",
            "selected_candidate": "",
            "candidate_count": 2,
            "official_source_found": True,
            "linkedin_source_found": True,
            "registry_source_found": False,
            "requested_role": "",
            "supported_role": "",
            "role_match": False,
            "role_mismatch_reason": "",
            "conflict_detected": False,
            "exact_role_verified": False,
            "evidence_strength": "medium",
            "strongest_source_type": "official_website",
            "search_depth_used": "standard",
            "search_lanes_used": ["official_website", "linkedin", "registry_directory", "general_web"],
            "source_tiers_found": ["tier1", "tier2"],
            "confidence_reason": "Public presence exists, but registry-grade proof is still limited.",
        }
        return {
            "answer": "Status: some_public_presence\nConfidence: Medium\nEvidence found:\n- Official website\n- LinkedIn company page",
            "route": case.expected_route,
            "selected_route": case.expected_route,
            "public_route_label": case.expected_route,
            "route_owner": case.expected_owner,
            "sources": ["https://relyce.ai", "https://linkedin.com/company/relyce-infotech"],
            "metadata": {"route_owner": case.expected_owner, "entity_intelligence_summary": summary, "evidence_stats": summary},
            "trust_block": {"answer_mode": "some_public_presence", "confidence_reason": summary["confidence_reason"], "official_source_found": True},
            "trace": {
                "entity_intelligence_summary": summary,
                "evidence_stats": summary,
                "extractor_candidates": [
                    {"title": "Relyce AI", "link": "https://relyce.ai", "provider": "official_website", "page_class": "official_website", "quality_score": 0.9, "rejection_reason": ""},
                    {"title": "Relyce LinkedIn", "link": "https://linkedin.com/company/relyce-infotech", "provider": "company_linkedin", "page_class": "company_linkedin", "quality_score": 0.84, "rejection_reason": ""},
                ],
            },
            "evidence_matrix_summary": {"supported_claims": 1, "unsupported_critical_claims": 0},
        }
    entity_query = EntityIntentDetector().detect(case.query)
    source_plan = EntitySourcePlanner().plan(entity_query)
    evidence = _mock_entity_evidence(case, entity_query)
    profiles = _mock_entity_profiles(case, entity_query)
    answer = EntityAnswerComposer().compose(entity_query=entity_query, evidence_rows=evidence, profile_candidates=profiles)
    summary = {
        "intent": entity_query.intent,
        "entity_name": entity_query.entity_name,
        "entity_type": entity_query.entity_type,
        "requested_attribute": entity_query.requested_attribute,
        "source_plan": {"lanes": source_plan.lanes, "required_lanes": list(source_plan.required_lanes)},
        "answer_mode": answer.mode,
        "confidence": answer.confidence,
        "why": answer.why,
        "verification_state": answer.verification_state,
        "selected_candidate": answer.selected_candidate,
        "candidate_count": answer.candidate_count,
        "requested_role": answer.requested_role,
        "supported_role": answer.supported_role,
        "role_match": answer.role_match,
        "role_mismatch_reason": answer.role_mismatch_reason,
        "conflict_detected": answer.conflict_detected,
        "exact_role_verified": answer.exact_role_verified,
        "official_source_found": answer.official_source_found,
        "linkedin_source_found": answer.linkedin_source_found,
        "registry_source_found": answer.registry_source_found,
        "strongest_source_type": answer.strongest_source_type,
        "evidence_strength": answer.evidence_strength,
        "search_depth_used": answer.search_depth_used,
        "search_lanes_used": list(answer.search_lanes_used),
        "source_tiers_found": list(answer.source_tiers_found),
        "confidence_reason": answer.confidence_reason,
    }
    trace_candidates = [_candidate_trace_row(row) for row in evidence]
    if not trace_candidates:
        trace_candidates = [_profile_trace_row(row) for row in profiles]
    trace = {
        "entity_intelligence_summary": summary,
        "evidence_stats": summary,
        "extractor_candidates": trace_candidates,
    }
    source_urls = [row.url for row in evidence if row.url]
    if not source_urls:
        source_urls = [row.url for row in profiles if row.url]
    return {
        "answer": answer.answer,
        "route": case.expected_route,
        "selected_route": case.expected_route,
        "public_route_label": case.expected_route,
        "route_owner": case.expected_owner,
        "entity_intelligence_summary": summary,
        "metadata": {
            "route_owner": case.expected_owner,
            "entity_intelligence_summary": summary,
            "evidence_stats": summary,
        },
        "trust_block": {
            "answer_mode": answer.mode,
            "confidence_reason": answer.confidence_reason,
            "official_source_found": answer.official_source_found,
        },
        "trace": trace,
        "evidence_matrix_summary": {
            "supported_claims": 1 if answer.exact_role_verified else 0,
            "unsupported_critical_claims": 0,
        },
        "sources": source_urls,
    }


def _execute_mock_research(case: LiveEvidenceCase) -> Dict[str, Any]:
    pipeline = ResearchPipeline()
    rows = _mock_research_rows(case)
    quality = pipeline.assess_source_quality(
        rows=rows,
        query=case.query,
        freshness_summary={"freshness_score": 0.88} if case.require_freshness else None,
        official_source_required=case.require_official_source,
    )
    policy = pipeline.answer_policy(
        quality_summary=quality["summary"],
        citation_coverage={"coverage": 0.8 if case.category != "research_rumour" else 0.0, "claims_supported": 2 if case.category != "research_rumour" else 0},
        conflict_summary={},
    )
    answer = pipeline.compose_research_answer(
        query=case.query,
        draft_answer=_mock_research_draft(case),
        evidence_rows=quality["usable_rows"],
        answer_policy=policy,
    )
    summary = pipeline.search_plan_summary(case.query)
    evidence_stats = {
        "query_plan_summary": summary,
        "answer_mode": policy.get("answer_mode"),
        "related_evidence_used": policy.get("related_evidence_used"),
        "confidence_reason": policy.get("confidence_reason"),
    }
    return {
        "answer": answer,
        "route": case.expected_route,
        "selected_route": case.expected_route,
        "public_route_label": case.expected_route,
        "route_owner": case.expected_owner,
        "metadata": {
            "route_owner": case.expected_owner,
            "evidence_stats": evidence_stats,
            "confidence_reason": policy.get("confidence_reason"),
            "query_plan_summary": summary,
        },
        "trust_block": {
            "answer_mode": policy.get("answer_mode"),
            "confidence_reason": policy.get("confidence_reason"),
            "related_evidence_used": policy.get("related_evidence_used"),
            "official_source_found": quality["summary"].get("official_source_count", 0) > 0,
            "freshness": policy.get("freshness_status"),
            "exact_claim_confirmed": policy.get("exact_claim_confirmed"),
            "conflict_detected": policy.get("conflict_detected"),
            "usable_sources_count": quality["summary"].get("usable_count", 0),
            "rejected_sources_count": quality.get("prefilter_summary", {}).get("rejected_results_count", 0),
        },
        "trace": {
            "evidence_stats": evidence_stats,
            "query_plan_summary": summary,
            "extractor_candidates": [_research_candidate_trace_row(row) for row in rows],
        },
        "evidence_matrix_summary": {
            "supported_claims": 2 if case.category != "research_rumour" else 0,
            "unsupported_critical_claims": 0,
        },
        "freshness_summary": {"freshness_mode": "current_or_recent"} if case.require_freshness else {},
        "sources": [row.get("link") for row in rows if row.get("link")],
    }


def _execute_live(case: LiveEvidenceCase, *, base_url: str, auth_token: str, timeout: float) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    token = auth_token or os.getenv("TAOS_LIVE_AUTH_TOKEN") or os.getenv("FIREBASE_ID_TOKEN") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(
        {
            "query": case.query,
            "user_id": "phase146_live_qa",
            "user_tier": "free",
            "include_trace": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(f"{base_url.rstrip('/')}/execute", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
            payload["_http_status"] = response.status
            return payload
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text or "{}")
        except json.JSONDecodeError:
            payload = {"answer": text}
        payload["_http_status"] = exc.code
        payload["_http_error"] = str(exc)
        return payload
    except Exception as exc:  # pragma: no cover - exercised in live mode
        return {"answer": "", "_http_status": 0, "_http_error": str(exc)}


def _build_evidence_matrix(
    *,
    payload: Mapping[str, Any],
    extractor_candidates: Iterable[Mapping[str, Any]],
    raw_search_results: Iterable[Mapping[str, Any]],
    trust_metadata: Mapping[str, Any],
    source_tiers_found: Sequence[str],
) -> List[Dict[str, Any]]:
    sources = list(payload.get("sources") or [])
    def _supports_exact_claim_for_row(row: Mapping[str, Any]) -> bool:
        evidence_source = str(row.get("evidence_source") or "")
        extracted_claim = str(row.get("snippet") or row.get("summary") or "").strip()
        role_holder = str(row.get("role_holder_detected") or row.get("candidate_name") or "").strip()
        extraction_quality = float(row.get("quality_score") or row.get("rank_score") or 0.0)
        role_applies = bool(row.get("role_applies_to_person"))
        company_match = bool(row.get("company_match"))
        entity_match = bool(row.get("target_entity_match"))
        if evidence_source == "source_link":
            return False
        if not extracted_claim and not role_holder:
            return False
        if extraction_quality <= 0 and not role_holder:
            return False
        if not role_applies and not role_holder:
            return False
        if not (company_match or entity_match):
            return False
        return bool(trust_metadata.get("exact_role_verified") or trust_metadata.get("role_match"))

    def _usable_for_verification(row: Mapping[str, Any]) -> bool:
        evidence_source = str(row.get("evidence_source") or "")
        extracted_claim = str(row.get("snippet") or row.get("summary") or "").strip()
        role_holder = str(row.get("role_holder_detected") or row.get("candidate_name") or "").strip()
        extraction_quality = float(row.get("quality_score") or row.get("rank_score") or 0.0)
        if str(row.get("rejection_reason") or "").strip():
            return False
        if evidence_source == "source_link":
            return False
        if extraction_quality <= 0 and not extracted_claim and not role_holder:
            return False
        return True

    rows: List[Dict[str, Any]] = []
    for idx, row in enumerate(extractor_candidates):
        link = str(row.get("link") or row.get("url") or "").strip()
        source_type = _infer_source_type(link=link, provider=str(row.get("provider") or ""), page_class=str(row.get("page_class") or ""))
        tier = _infer_tier(source_type, source_tiers_found, index=idx)
        rows.append(
            {
                "source_title": str(row.get("title") or row.get("query") or row.get("provider") or "").strip(),
                "domain": urlparse(link).netloc.replace("www.", "") if link else "",
                "source_type": source_type,
                "tier": tier,
                "extracted_claim": str(row.get("snippet") or row.get("summary") or "").strip(),
                "supported_role": str(trust_metadata.get("supported_role") or ""),
                "requested_role": str(trust_metadata.get("requested_role") or ""),
                "supports_exact_claim": _supports_exact_claim_for_row(row),
                "contradicts_claim": bool(
                    trust_metadata.get("requested_role")
                    and trust_metadata.get("supported_role")
                    and trust_metadata.get("requested_role") != trust_metadata.get("supported_role")
                ),
                "extraction_quality": row.get("quality_score") or row.get("rank_score") or 0.0,
                "freshness": str(trust_metadata.get("freshness_status") or ""),
                "usable_for_verification": _usable_for_verification(row),
                "rejection_reason": str(row.get("rejection_reason") or ""),
                "company_match": bool(row.get("company_match")),
                "target_entity_match": bool(row.get("target_entity_match")),
                "role_holder_detected": str(row.get("role_holder_detected") or row.get("candidate_name") or ""),
                "extracted_role": str(row.get("extracted_role") or ""),
                "role_applies_to_person": bool(row.get("role_applies_to_person")),
                "source_relevance_score": float(row.get("source_relevance_score") or 0.0),
                "extraction_status": str(row.get("extraction_status") or ""),
                "evidence_source": str(row.get("evidence_source") or ""),
            }
        )
    if not rows and raw_search_results:
        for idx, row in enumerate(raw_search_results):
            link = str(row.get("url") or row.get("link") or "").strip()
            source_type = _infer_source_type(link=link, provider=str(row.get("provider") or ""), page_class=str(row.get("page_class") or ""))
            rows.append(
                {
                    "source_title": str(row.get("title") or row.get("query") or row.get("provider") or "").strip(),
                    "domain": urlparse(link).netloc.replace("www.", "") if link else "",
                    "source_type": source_type,
                    "tier": _infer_tier(source_type, source_tiers_found, index=idx),
                    "extracted_claim": str(row.get("snippet") or "").strip(),
                    "supported_role": str(trust_metadata.get("supported_role") or ""),
                    "requested_role": str(trust_metadata.get("requested_role") or ""),
                    "supports_exact_claim": _supports_exact_claim_for_row(row),
                    "contradicts_claim": False,
                    "extraction_quality": 0.0,
                    "freshness": str(trust_metadata.get("freshness_status") or ""),
                    "usable_for_verification": _usable_for_verification(row),
                    "rejection_reason": "",
                    "company_match": False,
                    "target_entity_match": False,
                    "role_holder_detected": "",
                    "extracted_role": "",
                    "role_applies_to_person": False,
                    "source_relevance_score": 0.0,
                    "extraction_status": "search_only",
                    "evidence_source": "search_result_snippet",
                }
            )
    if not rows and sources:
        for idx, link in enumerate(sources[:5]):
            source_type = _infer_source_type(link=str(link), provider="", page_class="")
            rows.append(
                {
                    "source_title": str(link),
                    "domain": urlparse(str(link)).netloc.replace("www.", ""),
                    "source_type": source_type,
                    "tier": _infer_tier(source_type, source_tiers_found, index=idx),
                    "extracted_claim": "",
                    "supported_role": str(trust_metadata.get("supported_role") or ""),
                    "requested_role": str(trust_metadata.get("requested_role") or ""),
                    "supports_exact_claim": False,
                    "contradicts_claim": False,
                    "extraction_quality": 0.0,
                    "freshness": str(trust_metadata.get("freshness_status") or ""),
                    "usable_for_verification": False,
                    "rejection_reason": "source_link_only",
                    "company_match": False,
                    "target_entity_match": False,
                    "role_holder_detected": "",
                    "extracted_role": "",
                    "role_applies_to_person": False,
                    "source_relevance_score": 0.0,
                    "extraction_status": "source_link_only",
                    "evidence_source": "source_link",
                }
            )
    return rows


def _build_provider_diagnostics(*, payload: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = _dict(payload.get("metadata"))
    trace = _dict(payload.get("trace"))
    provider_health = _dict(trace.get("provider_health") or metadata.get("provider_health"))
    serper = _dict(provider_health.get("serper"))
    web_extract = _dict(provider_health.get("web_extract"))
    openrouter = _dict(provider_health.get("openrouter"))
    search_provider_ready = bool(serper) and str(serper.get("state") or "").lower() not in {"open"}
    extract_provider_ready = bool(web_extract) and str(web_extract.get("state") or "").lower() not in {"open"}
    network_error = (
        str(web_extract.get("last_error") or "")
        or str(serper.get("last_error") or "")
        or str(openrouter.get("last_error") or "")
    )
    external_network_ready = not any(token in network_error.lower() for token in ("connect", "resolve", "dns", "network"))
    search_error_type = str(_dict(payload.get("provider_diagnostics")).get("search_error_type") or serper.get("last_error") or "").strip()
    extract_error_type = str(_dict(payload.get("provider_diagnostics")).get("extract_error_type") or web_extract.get("last_error") or "").strip()
    return {
        "search_provider_ready": search_provider_ready,
        "search_provider_name": "serper" if (serper or search_provider_ready) else "",
        "search_api_key_present": bool(_dict(payload.get("provider_diagnostics")).get("search_api_key_present") or serper),
        "search_endpoint_configured": bool(_dict(payload.get("provider_diagnostics")).get("search_endpoint_configured") or serper),
        "search_http_status": _dict(payload.get("provider_diagnostics")).get("search_http_status"),
        "search_error_type": search_error_type[:80],
        "search_error_safe": network_error[:180],
        "extract_provider_ready": extract_provider_ready,
        "extract_error_type": extract_error_type[:80],
        "extract_error_safe": str(web_extract.get("last_error") or network_error)[:180],
        "external_network_ready": external_network_ready,
        "dns_resolution_ok": bool(_dict(payload.get("provider_diagnostics")).get("dns_resolution_ok")),
        "proxy_detected": bool(_dict(payload.get("provider_diagnostics")).get("proxy_detected")),
        "last_provider_error_safe": network_error[:180],
        "provider_mode": "live" if payload.get("_http_status") else "unknown",
        "live_provider_configured": bool(serper or web_extract or openrouter),
        "provider_connectivity_failed": bool(network_error) and not (search_provider_ready and extract_provider_ready),
    }


def _mock_entity_evidence(case: LiveEvidenceCase, entity_query: Any) -> List[EntityEvidence]:
    if case.id in {"relyce_ceo_role_truth", "relyce_founder_role_truth"}:
        requested_role = case.requested_role or "ceo"
        return [
            EntityEvidence(
                title="Relyce team page",
                url="https://relyce.ai/team",
                source_type="official_website",
                snippet="Tamizh Aruvi works as an engineer at Relyce Infotech.",
                candidate_name="Tamizh Aruvi",
                attribute=requested_role,
                requested_role=requested_role,
                supported_role="employee",
                role_match=False,
                role_mismatch_reason="employee_evidence_does_not_verify_requested_role",
                contradicts_claim=True,
                source_tier="tier1",
                strongest_source_type="official_website",
                extraction_quality=0.92,
                freshness="current_or_recent",
                usable_for_verification=True,
                rejection_reason="",
                supports_claim=False,
                confidence=0.92,
            ),
            EntityEvidence(
                title="Generic directory profile",
                url="https://directory.example/relyce",
                source_type="search_snippet",
                snippet="Tamizh Aruvi profile listing.",
                candidate_name="Tamizh Aruvi",
                attribute=requested_role,
                requested_role=requested_role,
                supported_role="employee",
                role_match=False,
                role_mismatch_reason="employee_evidence_does_not_verify_requested_role",
                contradicts_claim=True,
                source_tier="tier3",
                strongest_source_type="search_snippet",
                extraction_quality=0.35,
                freshness="unknown",
                usable_for_verification=False,
                rejection_reason="tier3_snippet_only",
                supports_claim=False,
                confidence=0.2,
            ),
        ]
    if case.id == "known_ceo_official_company":
        return [
            EntityEvidence(
                title="Microsoft leadership",
                url="https://www.microsoft.com/en-us/about/leadership",
                source_type="official_website",
                snippet="Satya Nadella is the Chairman and Chief Executive Officer of Microsoft.",
                candidate_name="Satya Nadella",
                attribute="ceo",
                requested_role="ceo",
                supported_role="ceo",
                role_match=True,
                source_tier="tier1",
                strongest_source_type="official_website",
                extraction_quality=0.97,
                freshness="current_or_recent",
                usable_for_verification=True,
                rejection_reason="",
                supports_claim=True,
                confidence=0.97,
            )
        ]
    if case.id == "known_founder_company":
        return [
            EntityEvidence(
                title="Microsoft history",
                url="https://news.microsoft.com/stories/people-who-inspired-us/bill-gates-and-paul-allen/",
                source_type="official_website",
                snippet="Bill Gates and Paul Allen founded Microsoft.",
                candidate_name="Bill Gates",
                attribute="founder",
                requested_role="founder",
                supported_role="founder",
                role_match=True,
                source_tier="tier1",
                strongest_source_type="official_website",
                extraction_quality=0.95,
                freshness="current_or_recent",
                usable_for_verification=True,
                rejection_reason="",
                supports_claim=True,
                confidence=0.95,
            )
        ]
    if case.id == "unknown_local_company_weak":
        return [
            EntityEvidence(
                title="Tiny Neem Labs listing",
                url="https://directory.example/tiny-neem-labs",
                source_type="search_snippet",
                snippet="Tiny Neem Labs profile page.",
                candidate_name="Unknown",
                attribute="ceo",
                requested_role="ceo",
                supported_role="",
                role_match=False,
                role_mismatch_reason="no_exact_role_evidence",
                source_tier="tier3",
                strongest_source_type="search_snippet",
                extraction_quality=0.22,
                freshness="unknown",
                usable_for_verification=False,
                rejection_reason="tier3_snippet_only",
                supports_claim=False,
                confidence=0.15,
            )
        ]
    return []


def _mock_entity_profiles(case: LiveEvidenceCase, entity_query: Any) -> List[EntityProfileCandidate]:
    if case.id == "relyce_linkedin":
        return [
            EntityProfileCandidate(
                handle="relyce-infotech",
                platform="linkedin",
                url="https://linkedin.com/company/relyce-infotech",
                evidence_type="company_linkedin",
                name_match=0.97,
                domain_match=True,
                linked_from_official_site=True,
                confidence=0.92,
            )
        ]
    if case.id == "relyce_official_site":
        return [
            EntityProfileCandidate(
                handle="relyce.ai",
                platform="website",
                url="https://relyce.ai",
                evidence_type="verified_social_link",
                name_match=0.95,
                domain_match=True,
                linked_from_official_site=True,
                confidence=0.91,
            )
        ]
    return []


def _mock_research_rows(case: LiveEvidenceCase) -> List[Dict[str, Any]]:
    if case.id == "claude_india_rumour":
        return [
            {
                "title": "Anthropic supported countries",
                "link": "https://support.anthropic.com/en/articles/8461763-supported-countries",
                "snippet": "Anthropic lists Claude as supported and available in India.",
                "tier": "official",
                "published_at": "2026-04-28",
                "provider": "anthropic",
            },
            {
                "title": "Reuters: RBI reviews Claude Mythos cyber risks",
                "link": "https://www.reuters.com/example",
                "snippet": "India's RBI and banks are reviewing cybersecurity risks around Anthropic's Claude Mythos model.",
                "tier": "reporting",
                "published_at": "2026-04-29",
                "provider": "reuters",
            },
        ]
    if case.id == "claude_supported_countries_official":
        return [
            {
                "title": "Claude supported countries",
                "link": "https://support.anthropic.com/en/articles/8461763-supported-countries",
                "snippet": "Claude is supported in India according to Anthropic supported countries.",
                "tier": "official",
                "published_at": "2026-04-28",
                "provider": "anthropic",
            },
            {
                "title": "Blog discussing supported regions",
                "link": "https://blog.example.com/claude-india",
                "snippet": "A blog summarizes Claude availability in India.",
                "tier": "other",
                "published_at": "2026-04-22",
                "provider": "blog",
            },
        ]
    return [
        {
            "title": "OpenAI changelog",
            "link": "https://openai.com/changelog",
            "snippet": "OpenAI announced current API updates in the changelog.",
            "tier": "official",
            "published_at": "2026-04-29",
            "provider": "openai",
        },
        {
            "title": "OpenAI docs models",
            "link": "https://platform.openai.com/docs/models",
            "snippet": "The official models documentation lists current model availability.",
            "tier": "official",
            "published_at": "2026-04-28",
            "provider": "openai",
        },
    ]


def _mock_research_draft(case: LiveEvidenceCase) -> str:
    if case.id == "claude_india_rumour":
        return ""
    if case.id == "claude_supported_countries_official":
        return "The best-supported answer is: Claude appears supported in India on Anthropic's supported countries page. [S1]"
    return "The best-supported answer is: OpenAI announced current API updates. [S1]"


def _candidate_trace_row(row: EntityEvidence) -> Dict[str, Any]:
    return {
        "title": row.title,
        "link": row.url,
        "snippet": row.snippet,
        "page_class": row.source_type,
        "provider": row.source_type,
        "quality_score": row.extraction_quality,
        "rejection_reason": row.rejection_reason,
    }


def _profile_trace_row(row: EntityProfileCandidate) -> Dict[str, Any]:
    return {
        "title": row.handle or row.url,
        "link": row.url,
        "snippet": f"{row.platform} profile candidate",
        "page_class": row.evidence_type,
        "provider": row.evidence_type,
        "quality_score": row.confidence,
        "rejection_reason": "",
    }


def _research_candidate_trace_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "title": str(row.get("title") or ""),
        "link": str(row.get("link") or ""),
        "snippet": str(row.get("snippet") or ""),
        "page_class": str(row.get("tier") or ""),
        "provider": str(row.get("provider") or ""),
        "quality_score": 0.8,
        "rejection_reason": "",
    }


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _summary_source_tiers(rows: Iterable[Mapping[str, Any]]) -> List[str]:
    out: List[str] = []
    for row in rows:
        tier = _infer_tier(_infer_source_type(link=str(row.get("link") or ""), provider=str(row.get("provider") or ""), page_class=str(row.get("page_class") or "")), (), 0)
        if tier not in out:
            out.append(tier)
    return out


def _infer_source_type(*, link: str, provider: str, page_class: str) -> str:
    lower = f"{link} {provider} {page_class}".lower()
    if "linkedin.com/company/" in lower:
        return "company_linkedin"
    if any(token in lower for token in ("anthropic.com", "openai.com", "microsoft.com", "official_website", "team", "leadership")):
        return "official_website"
    if any(token in lower for token in ("reuters", "forbes", "mint", "techcrunch", "yourstory")):
        return "reputable_news"
    if any(token in lower for token in ("registry", "mca", "company register", "directory")):
        return "registry_directory"
    return "search_snippet"


def _infer_tier(source_type: str, source_tiers_found: Sequence[str], index: int) -> str:
    if source_tiers_found and index < len(source_tiers_found):
        return str(source_tiers_found[index])
    if source_type in {"official_website", "government_registry", "company_linkedin"}:
        return "tier1"
    if source_type in {"reputable_news", "registry_directory", "person_linkedin"}:
        return "tier2"
    return "tier3"


def _answer_overclaims(fields: Mapping[str, Any]) -> bool:
    answer = str(fields.get("final_answer") or "").lower()
    trust = _dict(fields.get("trust_metadata"))
    if "could not verify" in answer or "do not treat" in answer:
        return False
    if trust.get("requested_role") in {"ceo", "founder"} and not trust.get("exact_role_verified"):
        if "best-supported public answer is" in answer or " is the ceo " in answer or " is the founder " in answer:
            return True
    return False


def _pass_reason(case: LiveEvidenceCase, fields: Mapping[str, Any], failed_checks: Sequence[str]) -> str:
    if failed_checks:
        return f"Failed checks: {', '.join(failed_checks)}"
    if bool(_dict(fields.get("provider_diagnostics")).get("provider_connectivity_failed")):
        return "Provider connectivity failed in this run, so unverified means unavailable-provider rather than searched-and-found-nothing."
    if case.expect_not_verified:
        return "The case correctly stayed unverified and avoided overclaiming."
    if case.expect_exact_role_verified:
        return "The case verified the exact role from stronger evidence."
    if case.expect_related_evidence:
        return "The case separated exact claim status from related evidence."
    return "The case matched route, trust metadata, and evidence requirements."


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Phase 147 live provider evidence QA")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--out-json", default=str(DEFAULT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_MD))
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)

    if args.live and not args.base_url:
        raise SystemExit("--live requires --base-url")

    report = run_cases(
        load_cases(args.cases, max_cases=args.max_cases),
        live=bool(args.live),
        base_url=args.base_url,
        auth_token=args.auth_token,
        timeout=args.timeout,
    )
    write_report(report, out_json=args.out_json, out_md=args.out_md)
    print(render_markdown(report))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
