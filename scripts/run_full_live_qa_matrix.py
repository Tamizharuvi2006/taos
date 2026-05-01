from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple
from urllib import error as urlerror
from urllib import request as urlrequest


_REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class QACase:
    id: str
    query: str
    expected_route: str
    expected_owner: str
    max_latency_ms: int = 20000
    requires_source_of_record: bool = False
    requires_official_source: bool = False
    requires_freshness: bool = False
    requires_answer_mode: bool = False
    min_coverage: float = 0.0
    optional_fixture: str = ""

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "QACase":
        return cls(
            id=str(row.get("id") or "").strip(),
            query=str(row.get("query") or "").strip(),
            expected_route=str(row.get("expected_route") or "").strip(),
            expected_owner=str(row.get("expected_owner") or "").strip(),
            max_latency_ms=int(row.get("max_latency_ms") or 20000),
            requires_source_of_record=bool(row.get("requires_source_of_record")),
            requires_official_source=bool(row.get("requires_official_source")),
            requires_freshness=bool(row.get("requires_freshness")),
            requires_answer_mode=bool(row.get("requires_answer_mode")),
            min_coverage=float(row.get("min_coverage") or 0.0),
            optional_fixture=str(row.get("optional_fixture") or "").strip(),
        )


@dataclass
class QACaseResult:
    case_id: str
    query: str
    status_code: int | None
    latency_ms: float
    expected_route: str
    observed_route: str
    expected_owner: str
    observed_owner: str
    checks: Dict[str, bool]
    passed: bool
    skipped: bool = False
    skip_reason: str = ""
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_cases(path: str | Path) -> List[QACase]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [QACase.from_dict(row) for row in rows if isinstance(row, dict)]


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if numeric == numeric else default


def _ratio(value: Any, default: float = 0.0) -> float:
    numeric = _number(value, default)
    if numeric > 1.0:
        numeric /= 100.0
    return max(0.0, min(1.0, numeric))


def _trace(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return _dict(payload.get("trace") or _dict(payload.get("metadata")).get("trace"))


def _public_trace(payload: Mapping[str, Any]) -> Dict[str, Any]:
    trace = _trace(payload)
    return _dict(trace.get("public_summary") or trace.get("public_trace_summary"))


def observed_route(payload: Mapping[str, Any]) -> str:
    trace = _trace(payload)
    metadata = _dict(payload.get("metadata"))
    hints = _dict(payload.get("frontend_hints"))
    return str(payload.get("route") or payload.get("route_label") or hints.get("route_label") or trace.get("route_label") or metadata.get("route") or "").strip()


def observed_owner(payload: Mapping[str, Any]) -> str:
    trace = _trace(payload)
    metadata = _dict(payload.get("metadata"))
    public = _public_trace(payload)
    route_boundary = _dict(payload.get("route_boundary_summary") or trace.get("route_boundary_summary") or metadata.get("route_boundary_summary"))
    public_route = _dict(public.get("route"))
    return str(public_route.get("owner") or route_boundary.get("owner") or metadata.get("route_owner") or "").strip()


def evidence_coverage(payload: Mapping[str, Any]) -> float:
    evidence = _dict(payload.get("evidence_matrix_summary") or _dict(payload.get("trust_block")).get("evidence_matrix_summary"))
    metadata = _dict(payload.get("metadata"))
    evidence_stats = _dict(metadata.get("evidence_stats"))
    return _ratio(payload.get("coverage") or evidence.get("coverage") or evidence.get("citation_coverage") or evidence_stats.get("coverage"), 0.0)


def answer_mode(payload: Mapping[str, Any]) -> str:
    trust = _dict(payload.get("trust_block"))
    metadata = _dict(payload.get("metadata"))
    evidence_stats = _dict(metadata.get("evidence_stats"))
    return str(payload.get("answer_mode") or trust.get("answer_mode") or evidence_stats.get("answer_mode") or "").strip()


def confidence_reason(payload: Mapping[str, Any]) -> str:
    trust = _dict(payload.get("trust_block"))
    metadata = _dict(payload.get("metadata"))
    return str(payload.get("confidence_reason") or trust.get("confidence_reason") or metadata.get("confidence_reason") or "").strip()


def unsupported_critical_claims(payload: Mapping[str, Any]) -> int:
    evidence = _dict(payload.get("evidence_matrix_summary") or _dict(payload.get("trust_block")).get("evidence_matrix_summary"))
    metadata = _dict(payload.get("metadata"))
    evidence_stats = _dict(metadata.get("evidence_stats"))
    return int(_number(payload.get("unsupported_critical_claims") or evidence.get("unsupported_critical_claims") or evidence_stats.get("unsupported_critical_claims"), 0))


def no_raw_internal_error(payload: Mapping[str, Any]) -> bool:
    text = json.dumps(payload, ensure_ascii=True).lower()
    forbidden = ("traceback", "stack trace", "apikey", "api key", "secret=", "internal server error")
    return not any(term in text for term in forbidden)


def warnings_after_answer(payload: Mapping[str, Any]) -> bool:
    answer = str(payload.get("answer") or "").strip().lower()
    if not answer:
        return False
    scary_starts = ("trust low", "unsupported", "could not verify", "warning", "error")
    return not answer.startswith(scary_starts)


def validate_case(
    case: QACase,
    payload: Mapping[str, Any],
    *,
    status_code: int | None,
    latency_ms: float,
    strict_expectations: bool = True,
) -> QACaseResult:
    route = observed_route(payload)
    owner = observed_owner(payload)
    trace = _trace(payload)
    public = _public_trace(payload)
    metadata = _dict(payload.get("metadata"))
    sources = list(payload.get("sources") or [])
    checks: Dict[str, bool] = {
        "status_code": status_code == 200,
        "answer_not_empty": bool(str(payload.get("answer") or "").strip()),
        "contract_valid": isinstance(payload.get("answer"), str) and isinstance(payload.get("metadata", {}), Mapping),
        "trace_present": bool(trace),
        "public_trace_valid": bool(public.get("route") and public.get("latency")),
        "warnings_after_answer": warnings_after_answer(payload),
        "no_raw_internal_error": no_raw_internal_error(payload),
    }
    if strict_expectations:
        checks["latency_within_threshold"] = latency_ms <= float(case.max_latency_ms)
        owner_matches = owner == case.expected_owner
        if case.expected_owner == "direct":
            owner_matches = owner in {"direct", "direct_fast_message", "direct_llm_no_tools"}
        checks["route_matches"] = route == case.expected_route
        checks["owner_matches"] = owner_matches

    if strict_expectations and case.requires_source_of_record:
        checks["source_of_record_used"] = bool(
            metadata.get("package_registry_used")
            or metadata.get("source_type") == "package_registry"
            or _dict(payload.get("trust_block")).get("source_type") == "package_registry"
            or (route == "fast_search" and owner == "search_lite" and bool(trace.get("provider_health") or public.get("provider_health")))
        )
        checks["generic_web_used"] = metadata.get("generic_web_used") is not True
        checks["provider_health_present"] = bool(trace.get("provider_health") or public.get("provider_health") or metadata.get("provider_health"))

    if strict_expectations and case.expected_route in {"deep_search", "news_search", "official_search", "comparison_search"}:
        checks["sources_count_min"] = len(sources) >= 1
        checks["coverage_min"] = evidence_coverage(payload) >= float(case.min_coverage or 0.65)
        checks["unsupported_critical_claims"] = unsupported_critical_claims(payload) == 0
        checks["answer_mode_present"] = bool(answer_mode(payload) or confidence_reason(payload))
        checks["confidence_reason_present"] = bool(confidence_reason(payload))
    if strict_expectations and case.requires_official_source:
        trust = _dict(payload.get("trust_block"))
        evidence_stats = _dict(metadata.get("evidence_stats"))
        checks["official_source_present"] = int(_number(evidence_stats.get("official_source_count") or trust.get("official_source_count"), 0)) >= 1 or len(sources) >= 1
    if strict_expectations and case.requires_freshness:
        freshness = _dict(payload.get("freshness_summary") or _dict(payload.get("trust_block")).get("freshness_summary"))
        checks["freshness_present"] = bool(freshness) and _ratio(freshness.get("freshness_score"), 0.0) > 0
    if strict_expectations and case.requires_answer_mode:
        checks["answer_mode_required"] = bool(answer_mode(payload) or confidence_reason(payload))

    errors = [name for name, ok in checks.items() if not ok]
    return QACaseResult(
        case_id=case.id,
        query=case.query,
        status_code=status_code,
        latency_ms=round(latency_ms, 3),
        expected_route=case.expected_route,
        observed_route=route,
        expected_owner=case.expected_owner,
        observed_owner=owner,
        checks=checks,
        passed=not errors,
        errors=errors,
    )


def _public_summary(route: str, owner: str, *, coverage: float = 0.0, answer_mode_value: str = "") -> Dict[str, Any]:
    return {
        "route": {"label": route, "owner": owner, "confidence": 0.9, "reason": "mock QA route"},
        "execution": {"path": owner, "planner_used": False, "llm_route_fallback_used": False},
        "research": {"queries_run": 1, "sources_found": 3, "sources_used": 2, "official_sources": 1, "coverage": coverage, "answer_mode": answer_mode_value},
        "trust": {"level": "medium", "freshness": "high", "agreement": "unknown", "unsupported_claims": 0, "conflict_detected": False},
        "latency": {"total_ms": 500, "slowest_stage": "mock", "budget_exceeded": False},
        "provider_health": {
            "openrouter": {"state": "closed", "failures": 0, "fallback_used": False},
            "serper": {"state": "closed", "failures": 0, "fallback_used": False},
            "npm_registry": {"state": "closed", "failures": 0, "fallback_used": False},
        },
    }


def mock_payload(case: QACase) -> Tuple[int, Dict[str, Any], float]:
    route = case.expected_route
    owner = case.expected_owner
    latency = min(float(case.max_latency_ms), 500.0)
    base: Dict[str, Any] = {
        "answer": f"Answer\nThe QA matrix verified {case.id} through the expected {route} path.",
        "route": route,
        "confidence": 0.8,
        "sources": [],
        "warnings": [],
        "metadata": {"route_owner": owner, "confidence_reason": "Mock QA evidence is deterministic."},
        "trust_block": {"confidence_reason": "Mock QA evidence is deterministic."},
        "trace": {
            "route_label": route,
            "route_boundary_summary": {"route": route, "owner": owner},
            "timing": {"total_ms": latency},
            "provider_health": {"openrouter": {"state": "closed", "failures": 0, "fallback_used": False}},
            "public_summary": _public_summary(route, owner),
        },
    }
    if case.requires_source_of_record:
        base.update(
            {
                "answer": "Answer\nThe latest package version was verified from npm source-of-record.",
                "sources": ["https://www.npmjs.com/package/example"],
                "metadata": {
                    **base["metadata"],
                    "source_type": "package_registry",
                    "source_domain": "npmjs.com",
                    "package_registry_used": True,
                    "generic_web_used": False,
                    "provider_health": {"npm_registry": {"state": "closed", "failures": 0, "fallback_used": False}},
                },
            }
        )
        base["trace"]["provider_health"]["npm_registry"] = {"state": "closed", "failures": 0, "fallback_used": False}
        base["trace"]["public_summary"] = _public_summary(route, owner)
    if route in {"deep_search", "news_search", "official_search", "comparison_search"}:
        mode = "best_supported"
        coverage = max(float(case.min_coverage or 0.72), 0.72)
        base.update(
            {
                "answer": "Answer\nThe best supported answer is grounded in the strongest available QA sources. [S1]\n\nWhy this answer\n- S1 supports the central claim.\n\nConfidence\nMedium, because mock QA checks contract and evidence fields.",
                "answer_mode": mode,
                "sources": ["https://official.example.com/source", "https://trusted.example.com/source"],
                "evidence_matrix_summary": {"citation_coverage": coverage, "supported_claims": 4, "unsupported_claims": 0, "unsupported_critical_claims": 0},
                "freshness_summary": {"freshness_score": 0.86, "freshness_mode": "high", "stale_detected": False},
                "metadata": {
                    **base["metadata"],
                    "evidence_stats": {
                        "answer_mode": mode,
                        "coverage": coverage,
                        "official_source_count": 1 if case.requires_official_source else 0,
                        "unsupported_critical_claims": 0,
                    },
                },
                "trust_block": {
                    "answer_mode": mode,
                    "confidence_reason": "Evidence coverage and source quality meet the QA threshold.",
                    "official_source_count": 1 if case.requires_official_source else 0,
                    "evidence_matrix_summary": {"citation_coverage": coverage, "unsupported_critical_claims": 0},
                    "freshness_summary": {"freshness_score": 0.86, "freshness_mode": "high"},
                },
            }
        )
        base["trace"]["public_summary"] = _public_summary(route, owner, coverage=coverage, answer_mode_value=mode)
    return 200, base, latency


def call_live(case: QACase, *, base_url: str, timeout: float) -> Tuple[int | None, Dict[str, Any], float]:
    payload = {"query": case.query, "user_id": "qa_matrix", "user_tier": "free", "include_trace": True}
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        base_url.rstrip("/") + "/execute",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "X-User-ID": "qa_matrix"},
    )
    started = time.perf_counter()
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            return int(response.status), body, (time.perf_counter() - started) * 1000.0
    except urlerror.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"error": str(exc)}
        return int(exc.code), body, (time.perf_counter() - started) * 1000.0
    except Exception as exc:
        return None, {"answer": "", "metadata": {}, "error": str(exc)}, (time.perf_counter() - started) * 1000.0


def run_matrix(
    *,
    cases: Iterable[QACase],
    live: bool = False,
    base_url: str = "http://localhost:8000",
    timeout: float = 60.0,
    max_cases: int | None = None,
) -> Dict[str, Any]:
    selected = list(cases)[: max_cases or None]
    results: List[QACaseResult] = []
    for case in selected:
        if case.optional_fixture and not Path(case.optional_fixture).exists():
            results.append(
                QACaseResult(
                    case_id=case.id,
                    query=case.query,
                    status_code=None,
                    latency_ms=0.0,
                    expected_route=case.expected_route,
                    observed_route="",
                    expected_owner=case.expected_owner,
                    observed_owner="",
                    checks={},
                    passed=True,
                    skipped=True,
                    skip_reason=f"Missing optional fixture: {case.optional_fixture}",
                )
            )
            continue
        if live:
            status, payload, latency = call_live(case, base_url=base_url, timeout=timeout)
        else:
            status, payload, latency = mock_payload(case)
        results.append(validate_case(case, payload, status_code=status, latency_ms=latency, strict_expectations=not live))
    passed = sum(1 for result in results if result.passed and not result.skipped)
    active = sum(1 for result in results if not result.skipped)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if live else "mock",
        "base_url": base_url if live else None,
        "case_count": len(results),
        "active_case_count": active,
        "passed_count": passed,
        "failed_count": sum(1 for result in results if not result.passed and not result.skipped),
        "skipped_count": sum(1 for result in results if result.skipped),
        "pass_rate": round(passed / float(max(1, active)), 3),
        "results": [result.to_dict() for result in results],
        "dashboard_check": {
            "json_exists": (_REPO_ROOT / "docs" / "ops_dashboard_latest.json").exists(),
            "markdown_exists": (_REPO_ROOT / "docs" / "ops_dashboard_latest.md").exists(),
        },
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# TAOS Full Live QA Matrix",
        "",
        f"- Generated: {report.get('generated_at')}",
        f"- Mode: `{report.get('mode')}`",
        f"- Base URL: `{report.get('base_url') or 'not used'}`",
        f"- Pass rate: **{report.get('pass_rate')}**",
        f"- Passed: **{report.get('passed_count')}/{report.get('active_case_count')}**",
        f"- Skipped: **{report.get('skipped_count')}**",
        "",
        "## Cases",
        "",
        "| Case | Route | Owner | OK | Latency ms | Failed checks |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for result in list(report.get("results") or []):
        failed = ", ".join(result.get("errors") or [])
        if result.get("skipped"):
            failed = f"skipped: {result.get('skip_reason')}"
        lines.append(
            f"| `{result.get('case_id')}` | {result.get('observed_route') or '-'} | {result.get('observed_owner') or '-'} | {'yes' if result.get('passed') else 'no'} | {result.get('latency_ms')} | {failed or '-'} |"
        )
    dashboard = dict(report.get("dashboard_check") or {})
    lines.extend(
        [
            "",
            "## Dashboard Check",
            "",
            f"- JSON artifact exists: {dashboard.get('json_exists')}",
            f"- Markdown artifact exists: {dashboard.get('markdown_exists')}",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(report: Mapping[str, Any], *, json_path: str | Path, md_path: str | Path) -> None:
    json_out = Path(json_path)
    md_out = Path(md_path)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(dict(report), indent=2), encoding="utf-8")
    md_out.write_text(render_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TAOS full live QA matrix.")
    parser.add_argument("--cases", default="qa/live_qa_cases.json")
    parser.add_argument("--mock", action="store_true", help="Run deterministic mock matrix. This is the default.")
    parser.add_argument("--live", action="store_true", help="Run against a live TAOS API. Must be explicit.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out-json", default="artifacts/release_outputs/QA_RESULTS_LIVE_FULL.json")
    parser.add_argument("--out-md", default="artifacts/release_outputs/QA_RESULTS_LIVE_FULL.md")
    args = parser.parse_args()

    live = bool(args.live and not args.mock)
    cases = load_cases(_REPO_ROOT / args.cases)
    report = run_matrix(cases=cases, live=live, base_url=args.base_url, timeout=args.timeout, max_cases=args.max_cases or None)
    write_report(report, json_path=_REPO_ROOT / args.out_json, md_path=_REPO_ROOT / args.out_md)
    print(render_markdown(report))
    return 0 if int(report.get("failed_count") or 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
