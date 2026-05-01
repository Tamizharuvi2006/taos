from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_PARENT = _REPO_ROOT.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))

from taos.core.research.claim_verification import ClaimVerificationAgent
from taos.core.search.query_planner_v2 import SearchQueryPlannerV2


DEFAULT_CASES = _REPO_ROOT / "qa" / "messy_query_live_cases.json"
DEFAULT_JSON = _REPO_ROOT / "QA_RESULTS_MESSY_QUERY.json"
DEFAULT_MD = _REPO_ROOT / "QA_RESULTS_MESSY_QUERY.md"
GENERIC_FAILURE_MARKERS = (
    "i couldn't verify this confidently",
    "i could not verify this confidently",
    "i couldn't verify this",
    "i could not verify this",
    "could not find reliable information",
)


@dataclass(frozen=True)
class MessyQueryCase:
    id: str
    query: str
    expected_intent: str = ""
    expected_entities: tuple[str, ...] = ()
    expected_relation: str = ""
    expected_route: str = ""
    requires_source_of_record: bool = False
    requires_official_lane: bool = False
    requires_news_lane: bool = False
    requires_contradiction_lane: bool = False
    requires_background_lane: bool = False
    requires_technical_lane: bool = False
    requires_rumour_status: bool = False
    requires_best_supported_status: bool = False
    requires_related_confusion: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row: Dict[str, Any]) -> "MessyQueryCase":
        return cls(
            id=str(row.get("id") or ""),
            query=str(row.get("query") or ""),
            expected_intent=str(row.get("expected_intent") or ""),
            expected_entities=tuple(str(item) for item in row.get("expected_entities") or ()),
            expected_relation=str(row.get("expected_relation") or ""),
            expected_route=str(row.get("expected_route") or ""),
            requires_source_of_record=bool(row.get("requires_source_of_record")),
            requires_official_lane=bool(row.get("requires_official_lane")),
            requires_news_lane=bool(row.get("requires_news_lane")),
            requires_contradiction_lane=bool(row.get("requires_contradiction_lane")),
            requires_background_lane=bool(row.get("requires_background_lane")),
            requires_technical_lane=bool(row.get("requires_technical_lane")),
            requires_rumour_status=bool(row.get("requires_rumour_status")),
            requires_best_supported_status=bool(row.get("requires_best_supported_status")),
            requires_related_confusion=bool(row.get("requires_related_confusion")),
            raw=dict(row),
        )


def load_cases(path: str | Path = DEFAULT_CASES, *, max_cases: int | None = None) -> List[MessyQueryCase]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [MessyQueryCase.from_dict(row) for row in rows]
    if max_cases is not None and max_cases >= 0:
        return cases[:max_cases]
    return cases


def run_cases(
    cases: Iterable[MessyQueryCase],
    *,
    live: bool = False,
    base_url: str = "http://localhost:8000",
    dev_user_id: str = "phase133-messy-query-qa",
    timeout: float = 60.0,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        if live:
            payload = _execute_live(case, base_url=base_url, dev_user_id=dev_user_id, timeout=timeout)
        else:
            payload = _execute_mock(case)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        result = validate_case(case, payload=payload, elapsed_ms=elapsed_ms, live=live)
        results.append(result)
    passed = sum(1 for item in results if item["ok"])
    failed = len(results) - passed
    return {
        "phase": "133",
        "mode": "live" if live else "mock",
        "base_url": base_url if live else "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / max(1, len(results)),
        "results": results,
    }


def validate_case(case: MessyQueryCase, *, payload: Dict[str, Any], elapsed_ms: float = 0.0, live: bool = False) -> Dict[str, Any]:
    answer = _answer_text(payload)
    answer_lower = answer.lower()
    query_plan = _query_plan_summary(payload)
    lanes = _lanes(query_plan)
    flattened = _flatten_lanes(lanes)
    first_query = flattened[0].strip().lower() if flattened else ""
    raw_query = case.query.strip().lower()
    entities = _entity_values(query_plan, payload)
    relation = str(_metadata(query_plan).get("relation") or query_plan.get("relation") or payload.get("relation") or "")
    route = _route(payload)
    failed: List[str] = []
    checks: Dict[str, bool] = {}

    checks["answer_not_empty"] = bool(answer.strip())
    checks["not_generic_failure"] = _not_generic_failure(answer_lower)
    checks["raw_query_preserved"] = _raw_query_preserved(case.query, query_plan, payload)
    checks["raw_query_not_primary"] = not first_query or first_query != raw_query
    checks["normalized_query_present"] = bool(query_plan.get("normalized_question") or payload.get("normalized_question"))
    checks["query_plan_summary_present"] = bool(query_plan)
    checks["sources_or_checked_sources_present"] = _has_sources(payload, answer_lower)
    checks["no_raw_internal_error"] = not _has_internal_error(answer)

    if case.expected_intent:
        checks["expected_intent"] = str(query_plan.get("intent") or payload.get("intent") or "") == case.expected_intent
    if case.expected_entities:
        entity_text = " ".join(sorted(entities)).lower()
        checks["expected_entities"] = all(expected.lower() in entity_text for expected in case.expected_entities)
    if case.expected_relation:
        checks["expected_relation"] = _relation_matches(case.expected_relation, relation)
    if case.expected_route:
        checks["expected_route"] = route == case.expected_route
    if case.requires_official_lane:
        checks["official_lane_present_when_required"] = bool(lanes.get("official"))
    if case.requires_news_lane:
        checks["news_lane_present_when_required"] = bool(lanes.get("news"))
    if case.requires_contradiction_lane:
        checks["contradiction_lane_present_when_required"] = bool(lanes.get("contradiction"))
    if case.requires_background_lane:
        checks["background_lane_present_when_required"] = bool(lanes.get("background"))
    if case.requires_technical_lane:
        checks["technical_lane_present_when_required"] = bool(lanes.get("technical"))
    if case.requires_rumour_status:
        checks["rumour_status_present_when_required"] = "rumour status" in answer_lower or "rumor status" in answer_lower
    if case.requires_best_supported_status:
        checks["best_supported_status_present"] = "best-supported status" in answer_lower or "best-supported answer" in answer_lower
    if case.requires_related_confusion:
        checks["related_evidence_or_confusion_present"] = "confusion" in answer_lower or "related" in answer_lower
    if case.requires_source_of_record:
        checks["package_source_of_record_present"] = _source_of_record_present(payload, lanes=lanes, answer=answer_lower)

    for name, ok in checks.items():
        if not ok:
            failed.append(name)

    return {
        "id": case.id,
        "query": case.query,
        "mode": "live" if live else "mock",
        "ok": not failed,
        "failed_checks": failed,
        "checks": checks,
        "elapsed_ms": elapsed_ms,
        "answer_preview": answer[:600],
        "route": route,
        "intent": query_plan.get("intent") or payload.get("intent") or "",
        "relation": relation,
        "entities": sorted(entities),
        "raw_query_priority": query_plan.get("raw_query_priority") or "",
        "primary_query": flattened[0] if flattened else "",
        "query_plan_summary": query_plan,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Phase 133 Messy Query Live QA",
        "",
        f"- Generated: {report['timestamp']}",
        f"- Mode: {report['mode']}",
        f"- Total: {report['total']}",
        f"- Passed: {report['passed']}",
        f"- Failed: {report['failed']}",
        f"- Pass rate: {report['pass_rate']:.3f}",
        "",
        "| Case | Route | Intent | Relation | OK | Failed checks | Primary query |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in report["results"]:
        failed = ", ".join(row.get("failed_checks") or []) or "-"
        primary = str(row.get("primary_query") or "").replace("|", "\\|")
        lines.append(
            f"| `{row['id']}` | {row.get('route') or '-'} | {row.get('intent') or '-'} | "
            f"{row.get('relation') or '-'} | {'yes' if row.get('ok') else 'no'} | {failed} | {primary} |"
        )
    lines.extend(["", "## Gate Checks", ""])
    lines.append("- Raw typo-heavy query is preserved for trace/debug but must not be primary search.")
    lines.append("- Query plan summary must include normalized question and source-aware lanes.")
    lines.append("- Rumour cases must show rumour status, best-supported status, and related/confusion context.")
    lines.append("- Package typo guard must keep source-of-record behavior.")
    return "\n".join(lines).strip() + "\n"


def write_report(report: Dict[str, Any], *, out_json: str | Path = DEFAULT_JSON, out_md: str | Path = DEFAULT_MD) -> None:
    Path(out_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    Path(out_md).write_text(render_markdown(report), encoding="utf-8")


def _execute_mock(case: MessyQueryCase) -> Dict[str, Any]:
    planner = SearchQueryPlannerV2()
    plan = planner.plan(case.query)
    query_plan = plan.summary()
    if case.requires_source_of_record:
        answer = (
            "Best-supported status: Vite version lookup remains a package source-of-record path.\n\n"
            "Evidence checked:\n- npm package registry source-of-record\n- Vite official package metadata"
        )
        return {
            "answer": answer,
            "route": "fast_search",
            "trace": {
                "route_label": "fast_search",
                "evidence_stats": {"query_plan_summary": query_plan},
            },
            "metadata": {
                "source_type": "package_registry",
                "package_registry_used": True,
                "generic_web_used": False,
                "evidence_stats": {"query_plan_summary": query_plan},
            },
            "sources": [{"title": "npm package registry", "url": "https://www.npmjs.com/package/vite"}],
        }

    rows = _mock_evidence_rows(plan=plan, case=case)
    verification = ClaimVerificationAgent().verify(query=case.query, evidence_rows=rows)
    answer = verification["answer"].replace("Best-supported answer:", "Best-supported status:")
    return {
        "answer": answer,
        "route": "deep_research",
        "intent": verification.get("query_plan", {}).get("intent") or query_plan.get("intent"),
        "trace": {
            "route_label": "deep_research",
            "evidence_stats": {"query_plan_summary": query_plan},
        },
        "metadata": {
            "evidence_stats": {"query_plan_summary": query_plan},
            "rumour_status": verification.get("status_label"),
            "best_supported_status": verification.get("best_supported"),
        },
        "sources": rows,
    }


def _mock_evidence_rows(*, plan, case: MessyQueryCase) -> List[Dict[str, str]]:
    entities = dict(plan.metadata.get("entities") or {})
    product = str(entities.get("product") or entities.get("company") or "Service")
    country = str(entities.get("country") or "region")
    official = f"{product} supported countries {country}".strip()
    return [
        {
            "title": f"{official} official availability",
            "snippet": f"Official source indicates {product} appears supported or available for {country}.",
            "url": "https://example.com/official",
        },
        {
            "title": f"{product} outage or service issue report",
            "snippet": "Related outage reports may explain user confusion but do not confirm a national block.",
            "url": "https://example.com/outage",
        },
        {
            "title": f"{product} security or account suspension coverage",
            "snippet": "Security, regulatory concern, or account suspension stories are related but not the exact claim.",
            "url": "https://example.com/background",
        },
    ]


def _execute_live(case: MessyQueryCase, *, base_url: str, dev_user_id: str, timeout: float) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/execute"
    body = json.dumps(
        {
            "query": case.query,
            "include_trace": True,
            "user_id": dev_user_id,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-User-ID": dev_user_id,
            "X-Dev-User-ID": dev_user_id,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            payload = json.loads(text) if text.strip() else {}
            payload.setdefault("_http_status", response.status)
            return payload
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            payload = {"answer": text}
        payload["_http_status"] = exc.code
        return payload
    except Exception as exc:
        return {"answer": "", "_http_error": str(exc), "_http_status": 0}


def _query_plan_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    candidates = [
        payload.get("query_plan_summary"),
        payload.get("query_plan"),
        _nested(payload, "metadata", "query_plan_summary"),
        _nested(payload, "metadata", "evidence_stats", "query_plan_summary"),
        _nested(payload, "trace", "query_plan_summary"),
        _nested(payload, "trace", "evidence_stats", "query_plan_summary"),
        _nested(payload, "evidence_stats", "query_plan_summary"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _metadata(query_plan: Dict[str, Any]) -> Dict[str, Any]:
    meta = query_plan.get("metadata")
    return dict(meta) if isinstance(meta, dict) else {}


def _lanes(query_plan: Dict[str, Any]) -> Dict[str, List[str]]:
    raw = query_plan.get("lanes") or query_plan.get("search_plan") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(name): [str(item) for item in (items or [])] for name, items in raw.items()}


def _flatten_lanes(lanes: Dict[str, List[str]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for name in ("official", "news", "contradiction", "background", "technical", "regional", "fallback"):
        for query in lanes.get(name) or []:
            key = query.lower().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(query)
    return out


def _entity_values(query_plan: Dict[str, Any], payload: Dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for source in (_metadata(query_plan).get("entities"), query_plan.get("entities"), payload.get("entities")):
        if isinstance(source, dict):
            values.update(str(value) for value in source.values() if str(value).strip())
        elif isinstance(source, list):
            values.update(str(value) for value in source if str(value).strip())
    return values


def _answer_text(payload: Dict[str, Any]) -> str:
    for key in ("answer", "response", "text", "content", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        return _answer_text(data)
    return ""


def _route(payload: Dict[str, Any]) -> str:
    candidates = (
        payload.get("route"),
        payload.get("selected_route"),
        payload.get("route_label"),
        _nested(payload, "trace", "route_label"),
        _nested(payload, "trace", "selected_route"),
        _nested(payload, "metadata", "route"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _raw_query_preserved(query: str, query_plan: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    raw = query.strip().lower()
    candidates = (
        query_plan.get("original_query"),
        payload.get("original_query"),
        _nested(payload, "trace", "original_query"),
        _nested(payload, "metadata", "original_query"),
    )
    return any(isinstance(candidate, str) and candidate.strip().lower() == raw for candidate in candidates)


def _relation_matches(expected: str, observed: str) -> bool:
    expected_norm = expected.lower().strip()
    observed_norm = observed.lower().strip()
    if expected_norm == observed_norm:
        return True
    if expected_norm == "outage" and observed_norm == "unavailable_or_outage":
        return True
    return False


def _source_of_record_present(payload: Dict[str, Any], *, lanes: Dict[str, List[str]], answer: str) -> bool:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    source_type = str(metadata.get("source_type") or payload.get("source_type") or "").lower()
    return (
        source_type == "package_registry"
        or bool(metadata.get("package_registry_used"))
        or ("npm" in answer and "source-of-record" in answer)
        or any("vite" in item.lower() for item in lanes.get("technical") or [])
    )


def _not_generic_failure(answer_lower: str) -> bool:
    stripped = answer_lower.strip()
    if not stripped:
        return False
    return not any(stripped.startswith(marker) for marker in GENERIC_FAILURE_MARKERS)


def _has_sources(payload: Dict[str, Any], answer_lower: str) -> bool:
    sources = payload.get("sources") or payload.get("citations") or []
    return bool(sources) or "evidence checked" in answer_lower or "sources checked" in answer_lower


def _has_internal_error(answer: str) -> bool:
    lowered = answer.lower()
    return any(marker in lowered for marker in ("traceback", "openrouter_api_key", "firebase_credentials", "d:\\", "c:\\users\\"))


def _nested(value: Dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Phase 133 messy query understanding QA")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true", help="Run deterministic local QA without calling a backend")
    mode.add_argument("--live", action="store_true", help="Run against a live backend /execute endpoint")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--dev-user-id", default="phase133-messy-query-qa")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out-json", default=str(DEFAULT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_MD))
    args = parser.parse_args(argv)

    cases = load_cases(args.cases, max_cases=args.max_cases)
    report = run_cases(
        cases,
        live=bool(args.live),
        base_url=args.base_url,
        dev_user_id=args.dev_user_id,
        timeout=args.timeout,
    )
    write_report(report, out_json=args.out_json, out_md=args.out_md)
    print(render_markdown(report))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
