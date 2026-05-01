from __future__ import annotations

import argparse
import json
import os
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

from taos.core.routing.route_cache import RouteCache
from taos.core.routing.route_decider import RouteDecider
from taos.core.understanding.universal_understanding_gateway import frame_to_trace_summary
from taos.orchestration.route_dispatcher import RouteDispatcher


DEFAULT_CASES = _REPO_ROOT / "qa" / "universal_understanding_live_cases.json"
DEFAULT_JSON = _REPO_ROOT / "QA_RESULTS_UNIVERSAL_UNDERSTANDING.json"
DEFAULT_MD = _REPO_ROOT / "QA_RESULTS_UNIVERSAL_UNDERSTANDING.md"
GENERIC_FAILURE_MARKERS = (
    "i couldn't verify this confidently",
    "i could not verify this confidently",
    "i couldn't verify this",
    "i could not verify this",
    "could not find reliable information",
)


@dataclass(frozen=True)
class UniversalUnderstandingCase:
    id: str
    query: str
    expected_route: str
    expected_owner: str = ""
    expected_intent_hint: str = ""
    expected_normalized_contains: tuple[str, ...] = ()
    expected_entity: str = ""
    expected_entities: tuple[str, ...] = ()
    expected_document_mode: str = ""
    expected_mark_format: str = ""
    expected_task_hint: str = ""
    expected_code_hint: str = ""
    requires_source_of_record: bool = False
    requires_query_plan: bool = False
    requires_official_lane: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row: Dict[str, Any]) -> "UniversalUnderstandingCase":
        return cls(
            id=str(row.get("id") or ""),
            query=str(row.get("query") or ""),
            expected_route=str(row.get("expected_route") or ""),
            expected_owner=str(row.get("expected_owner") or ""),
            expected_intent_hint=str(row.get("expected_intent_hint") or ""),
            expected_normalized_contains=tuple(str(item) for item in row.get("expected_normalized_contains") or ()),
            expected_entity=str(row.get("expected_entity") or ""),
            expected_entities=tuple(str(item) for item in row.get("expected_entities") or ()),
            expected_document_mode=str(row.get("expected_document_mode") or ""),
            expected_mark_format=str(row.get("expected_mark_format") or ""),
            expected_task_hint=str(row.get("expected_task_hint") or ""),
            expected_code_hint=str(row.get("expected_code_hint") or ""),
            requires_source_of_record=bool(row.get("requires_source_of_record")),
            requires_query_plan=bool(row.get("requires_query_plan")),
            requires_official_lane=bool(row.get("requires_official_lane")),
            raw=dict(row),
        )


def load_cases(path: str | Path = DEFAULT_CASES, *, max_cases: int | None = None) -> List[UniversalUnderstandingCase]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [UniversalUnderstandingCase.from_dict(row) for row in rows]
    if max_cases is not None and max_cases >= 0:
        return cases[:max_cases]
    return cases


def run_cases(
    cases: Iterable[UniversalUnderstandingCase],
    *,
    live: bool = False,
    base_url: str = "http://localhost:8000",
    auth_token: str = "",
    dev_user_id: str = "phase134-universal-understanding-qa",
    timeout: float = 60.0,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        payload = (
            _execute_live(case, base_url=base_url, auth_token=auth_token, dev_user_id=dev_user_id, timeout=timeout)
            if live
            else _execute_mock(case)
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        results.append(validate_case(case, payload=payload, elapsed_ms=elapsed_ms, live=live))
    passed = sum(1 for item in results if item["ok"])
    failed = len(results) - passed
    return {
        "phase": "134",
        "mode": "live" if live else "mock",
        "base_url": base_url if live else "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / max(1, len(results)),
        "results": results,
    }


def validate_case(
    case: UniversalUnderstandingCase,
    *,
    payload: Dict[str, Any],
    elapsed_ms: float = 0.0,
    live: bool = False,
) -> Dict[str, Any]:
    trace = _trace(payload)
    frame = _intent_frame(payload)
    answer = _answer_text(payload)
    route = _route(payload, frame)
    owner = _owner(payload)
    normalized = str(frame.get("normalized_query") or "")
    query_plan = _query_plan_summary(frame, payload)
    lanes = _lanes(query_plan)
    entities = _entity_values(frame)
    checks: Dict[str, bool] = {}
    failed: List[str] = []

    checks["answer_not_empty"] = bool(answer.strip())
    checks["route_matches"] = route == case.expected_route
    checks["owner_matches"] = _owner_matches(case.expected_owner, owner)
    checks["original_query_preserved"] = str(frame.get("original_query") or "").strip().lower() == case.query.strip().lower()
    checks["normalized_query_present"] = bool(normalized.strip())
    checks["intent_frame_present"] = bool(frame)
    checks["route_hint_present"] = bool(frame.get("route_hint"))
    checks["handler_owner_present"] = bool(owner)
    checks["public_trace_valid"] = bool(trace) and not _has_internal_error(json.dumps(trace, default=str))
    checks["no_raw_internal_error"] = not _has_internal_error(answer + json.dumps(payload, default=str)[:2000])

    if case.expected_intent_hint:
        checks["intent_hint_matches"] = str(frame.get("intent_hint") or "") == case.expected_intent_hint
    if case.expected_normalized_contains:
        checks["normalized_contains_expected_terms"] = all(term.lower() in normalized.lower() for term in case.expected_normalized_contains)
    if case.expected_entity:
        checks["expected_entity_present"] = case.expected_entity.lower() in " ".join(sorted(entities)).lower()
    if case.expected_entities:
        entity_text = " ".join(sorted(entities)).lower()
        checks["expected_entities_present"] = all(entity.lower() in entity_text for entity in case.expected_entities)
    if case.requires_source_of_record:
        checks["source_of_record_used"] = _source_of_record_used(payload, frame)
        checks["generic_web_used_false"] = not _generic_web_used(payload)
    if case.requires_query_plan:
        checks["query_plan_summary_present"] = bool(query_plan)
        checks["raw_query_not_primary"] = _raw_query_not_primary(case.query, lanes)
        checks["not_generic_failure"] = _not_generic_failure(answer)
    if case.requires_official_lane:
        checks["official_lane_present"] = bool(lanes.get("official"))
    if case.expected_document_mode:
        checks["document_mode_hint_present"] = str((frame.get("document_hints") or {}).get("mode") or "") == case.expected_document_mode
    if case.expected_mark_format:
        checks["document_mark_format_present"] = str((frame.get("document_hints") or {}).get("mark_format") or "") == case.expected_mark_format
    if case.expected_task_hint:
        checks["task_hint_present"] = str((frame.get("task_hints") or {}).get("action") or "") == case.expected_task_hint
    if case.expected_code_hint:
        checks["code_hint_present"] = str((frame.get("code_hints") or {}).get("error_type") or "") == case.expected_code_hint
    if case.expected_document_mode or case.expected_task_hint or case.expected_code_hint:
        checks["route_uses_understanding_frame"] = _route_uses_understanding(payload, frame)

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
        "owner": owner,
        "normalized_query": normalized,
        "intent_hint": frame.get("intent_hint") or "",
        "route_hint": frame.get("route_hint") or "",
        "entities": sorted(entities),
        "query_plan_summary": query_plan,
        "http_status": payload.get("_http_status", ""),
        "http_error": payload.get("_http_error", ""),
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Phase 134 Universal Understanding QA",
        "",
        f"- Generated: {report['timestamp']}",
        f"- Mode: {report['mode']}",
        f"- Total: {report['total']}",
        f"- Passed: {report['passed']}",
        f"- Failed: {report['failed']}",
        f"- Pass rate: {report['pass_rate']:.3f}",
        "",
        "| Case | Route | Owner | Route hint | OK | Failed checks | Normalized query |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in report["results"]:
        failed = ", ".join(row.get("failed_checks") or []) or "-"
        normalized = str(row.get("normalized_query") or "").replace("|", "\\|")
        lines.append(
            f"| `{row['id']}` | {row.get('route') or '-'} | {row.get('owner') or '-'} | "
            f"{row.get('route_hint') or '-'} | {'yes' if row.get('ok') else 'no'} | {failed} | {normalized} |"
        )
    lines.extend(["", "## Contract Checks", ""])
    lines.append("- Universal understanding frame must appear in trace/public trace.")
    lines.append("- Original and normalized query must be preserved.")
    lines.append("- Route hint, handler owner, and route must align.")
    lines.append("- Package, research, document, task, code, comparison, and clarification paths are locked.")
    return "\n".join(lines).strip() + "\n"


def write_report(report: Dict[str, Any], *, out_json: str | Path = DEFAULT_JSON, out_md: str | Path = DEFAULT_MD) -> None:
    Path(out_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    Path(out_md).write_text(render_markdown(report), encoding="utf-8")


def _execute_mock(case: UniversalUnderstandingCase) -> Dict[str, Any]:
    RouteCache().clear()
    decision = RouteDecider().decide_sync_for_tests(case.query)
    frame = decision.routing_signals.get("universal_understanding") or {}
    route = decision.route
    owner = _contract_owner(RouteDispatcher().owner_for_route(route))
    metadata: Dict[str, Any] = {
        "route_owner": owner,
        "generic_web_used": False,
        "universal_understanding": frame,
    }
    if case.requires_source_of_record:
        metadata.update({"source_type": "package_registry", "package_registry_used": True})
    answer = _mock_answer(case=case, route=route, frame=frame)
    return {
        "answer": answer,
        "route": route,
        "metadata": metadata,
        "sources": [{"title": "Mock contract source"}],
        "trace": {
            "route_label": route,
            "route_source": "phase134_mock",
            "route_decision": {
                **decision.to_dict(),
                "route_owner": owner,
                "universal_understanding": frame,
            },
            "route_boundary_summary": {
                "selected_route": route,
                "owner": owner,
                "boundary": decision.boundary,
            },
            "universal_understanding": frame,
        },
    }


def _mock_answer(*, case: UniversalUnderstandingCase, route: str, frame: Dict[str, Any]) -> str:
    if route == "clarification":
        return "I need one more detail before I can do that safely."
    if case.requires_query_plan:
        return "Rumour status: Not confirmed.\nBest-supported status: query-plan lanes are available for verification."
    if case.requires_source_of_record:
        return "Best-supported status: package source-of-record lookup is selected."
    return f"Handled by {route} using normalized query: {frame.get('normalized_query') or case.query}"


def _execute_live(
    case: UniversalUnderstandingCase,
    *,
    base_url: str,
    auth_token: str,
    dev_user_id: str,
    timeout: float,
) -> Dict[str, Any]:
    token = auth_token or os.getenv("TAOS_LIVE_AUTH_TOKEN") or os.getenv("FIREBASE_ID_TOKEN") or ""
    url = f"{base_url.rstrip('/')}/execute"
    body = json.dumps({"query": case.query, "include_trace": True, "user_id": dev_user_id}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-User-ID": dev_user_id,
        "X-Dev-User-ID": dev_user_id,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
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


def _trace(payload: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("trace", "public_trace", "execution_trace"):
        value = payload.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}


def _intent_frame(payload: Dict[str, Any]) -> Dict[str, Any]:
    trace = _trace(payload)
    candidates = [
        payload.get("universal_understanding"),
        _nested(payload, "metadata", "universal_understanding"),
        _nested(payload, "route_decision", "universal_understanding"),
        _nested(trace, "universal_understanding"),
        _nested(trace, "route_decision", "universal_understanding"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _query_plan_summary(frame: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    candidates = [
        frame.get("query_plan_summary"),
        payload.get("query_plan_summary"),
        _nested(payload, "metadata", "query_plan_summary"),
        _nested(_trace(payload), "evidence_stats", "query_plan_summary"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _lanes(query_plan: Dict[str, Any]) -> Dict[str, List[str]]:
    raw = query_plan.get("lanes") or query_plan.get("search_plan") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(name): [str(item) for item in (items or [])] for name, items in raw.items()}


def _route(payload: Dict[str, Any], frame: Dict[str, Any]) -> str:
    trace = _trace(payload)
    for candidate in (
        payload.get("route"),
        payload.get("selected_route"),
        payload.get("route_label"),
        _nested(trace, "route_label"),
        _nested(trace, "route_decision", "route"),
        _nested(trace, "route_decision", "phase107_route"),
        frame.get("route_hint"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return ""


def _owner(payload: Dict[str, Any]) -> str:
    trace = _trace(payload)
    for candidate in (
        payload.get("owner"),
        payload.get("route_owner"),
        _nested(payload, "metadata", "route_owner"),
        _nested(trace, "route_boundary_summary", "owner"),
        _nested(trace, "route_decision", "route_owner"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return _contract_owner(candidate)
    route = _route(payload, _intent_frame(payload))
    if route:
        return _contract_owner(RouteDispatcher().owner_for_route(route))
    return ""


def _contract_owner(owner: str) -> str:
    value = str(owner or "").strip().lower()
    if value in {"direct_fast_message", "direct_llm_no_tools", "direct_standard"}:
        return "direct"
    if value == "clarification_fallback":
        return "clarification"
    return value


def _owner_matches(expected: str, observed: str) -> bool:
    if not expected:
        return True
    return _contract_owner(expected) == _contract_owner(observed)


def _entity_values(frame: Dict[str, Any]) -> set[str]:
    values: set[str] = set()
    entities = frame.get("entities")
    if isinstance(entities, dict):
        values.update(str(value) for value in entities.values() if str(value).strip())
    candidates = frame.get("correction_candidates")
    if isinstance(candidates, list):
        values.update(str(item.get("candidate")) for item in candidates if isinstance(item, dict) and str(item.get("candidate") or "").strip())
    code_hints = frame.get("code_hints")
    if isinstance(code_hints, dict) and code_hints.get("framework"):
        values.add(str(code_hints["framework"]))
    return values


def _source_of_record_used(payload: Dict[str, Any], frame: Dict[str, Any]) -> bool:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return (
        str(metadata.get("source_type") or "").lower() == "package_registry"
        or bool(metadata.get("package_registry_used"))
        or str(frame.get("relation") or "") == "latest_version"
    )


def _generic_web_used(payload: Dict[str, Any]) -> bool:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return bool(metadata.get("generic_web_used"))


def _raw_query_not_primary(query: str, lanes: Dict[str, List[str]]) -> bool:
    for lane in ("official", "news", "contradiction", "background", "technical", "regional", "fallback"):
        for candidate in lanes.get(lane) or []:
            return str(candidate).strip().lower() != query.strip().lower()
    return False


def _not_generic_failure(answer: str) -> bool:
    lowered = str(answer or "").strip().lower()
    return bool(lowered) and not any(lowered.startswith(marker) for marker in GENERIC_FAILURE_MARKERS)


def _route_uses_understanding(payload: Dict[str, Any], frame: Dict[str, Any]) -> bool:
    trace = _trace(payload)
    boundary = str(_nested(trace, "route_decision", "boundary") or "").lower()
    return bool(frame) and (boundary == "universal_understanding" or _route(payload, frame) == str(frame.get("route_hint") or "").lower())


def _answer_text(payload: Dict[str, Any]) -> str:
    for key in ("answer", "response", "text", "content", "message", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        return _answer_text(data)
    return ""


def _has_internal_error(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in ("traceback", "openrouter_api_key", "firebase_credentials", "d:\\", "c:\\users\\"))


def _nested(value: Dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Phase 134 universal understanding route-contract QA")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true", help="Run deterministic local contract QA")
    mode.add_argument("--live", action="store_true", help="Run against a live backend /execute endpoint")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--dev-user-id", default="phase134-universal-understanding-qa")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out-json", default=str(DEFAULT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_MD))
    args = parser.parse_args(argv)

    report = run_cases(
        load_cases(args.cases, max_cases=args.max_cases),
        live=bool(args.live),
        base_url=args.base_url,
        auth_token=args.auth_token,
        dev_user_id=args.dev_user_id,
        timeout=args.timeout,
    )
    write_report(report, out_json=args.out_json, out_md=args.out_md)
    print(render_markdown(report))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
