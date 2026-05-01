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

from taos.core.entity import (
    EntityAnswerComposer,
    EntityEvidence,
    EntityIntent,
    EntityIntentDetector,
    EntityProfileCandidate,
    EntitySourcePlanner,
)


DEFAULT_CASES = _REPO_ROOT / "qa" / "entity_discovery_live_cases.json"
DEFAULT_JSON = _REPO_ROOT / "QA_RESULTS_ENTITY_DISCOVERY.json"
DEFAULT_MD = _REPO_ROOT / "QA_RESULTS_ENTITY_DISCOVERY.md"


@dataclass(frozen=True)
class EntityDiscoveryCase:
    id: str
    query: str
    expected_intent: str
    expected_entity_type: str = ""
    requires_entity_lanes: tuple[str, ...] = ()
    requires_social_lane: bool = False
    requires_linkedin_lane: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row: Dict[str, Any]) -> "EntityDiscoveryCase":
        return cls(
            id=str(row.get("id") or ""),
            query=str(row.get("query") or ""),
            expected_intent=str(row.get("expected_intent") or ""),
            expected_entity_type=str(row.get("expected_entity_type") or ""),
            requires_entity_lanes=tuple(str(item) for item in row.get("requires_entity_lanes") or ()),
            requires_social_lane=bool(row.get("requires_social_lane")),
            requires_linkedin_lane=bool(row.get("requires_linkedin_lane")),
            raw=dict(row),
        )


def load_cases(path: str | Path = DEFAULT_CASES, *, max_cases: int | None = None) -> List[EntityDiscoveryCase]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [EntityDiscoveryCase.from_dict(row) for row in rows]
    return cases[:max_cases] if max_cases is not None and max_cases >= 0 else cases


def run_cases(
    cases: Iterable[EntityDiscoveryCase],
    *,
    live: bool = False,
    base_url: str = "http://localhost:8000",
    auth_token: str = "",
    timeout: float = 60.0,
) -> Dict[str, Any]:
    results = []
    for case in cases:
        started = time.perf_counter()
        payload = _execute_live(case, base_url=base_url, auth_token=auth_token, timeout=timeout) if live else _execute_mock(case)
        results.append(validate_case(case, payload=payload, elapsed_ms=round((time.perf_counter() - started) * 1000, 2), live=live))
    passed = sum(1 for row in results if row["ok"])
    failed = len(results) - passed
    return {
        "phase": "136",
        "mode": "live" if live else "mock",
        "base_url": base_url if live else "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / max(1, len(results)),
        "results": results,
    }


def validate_case(case: EntityDiscoveryCase, *, payload: Dict[str, Any], elapsed_ms: float = 0.0, live: bool = False) -> Dict[str, Any]:
    summary = _summary(payload)
    plan = summary.get("source_plan") if isinstance(summary.get("source_plan"), dict) else {}
    answer = _answer(payload)
    checks = {
        "entity_extracted": bool(summary.get("entity_name")),
        "intent_matches": str(summary.get("intent") or "") == case.expected_intent,
        "source_lanes_generated": bool(plan.get("lanes")),
        "answer_mode_present": bool(summary.get("answer_mode")),
        "confidence_reason_present": bool(summary.get("confidence") and summary.get("why")),
        "no_private_data_scraping": not _private_policy_violation(payload),
        "no_overclaiming_weak_evidence": str(summary.get("answer_mode") or "").lower() not in {"verified_entity_fact", "official_profile_verified"} or bool(summary.get("verified_strong_source")),
        "answer_not_empty": bool(answer.strip()),
    }
    if case.expected_entity_type:
        checks["entity_type_matches"] = str(summary.get("entity_type") or "") == case.expected_entity_type
    for lane in case.requires_entity_lanes:
        checks[f"lane_{lane}_present"] = lane in (plan.get("lanes") or {})
    if case.requires_social_lane:
        checks["social_lane_present"] = bool((plan.get("lanes") or {}).get("social_profiles"))
    if case.requires_linkedin_lane:
        checks["linkedin_lane_present"] = bool((plan.get("lanes") or {}).get("linkedin"))
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "id": case.id,
        "query": case.query,
        "mode": "live" if live else "mock",
        "ok": not failed,
        "failed_checks": failed,
        "checks": checks,
        "elapsed_ms": elapsed_ms,
        "entity_name": summary.get("entity_name") or "",
        "intent": summary.get("intent") or "",
        "answer_mode": summary.get("answer_mode") or "",
        "confidence": summary.get("confidence") or "",
        "source_lanes": sorted((plan.get("lanes") or {}).keys()),
        "answer_preview": answer[:500],
        "http_status": payload.get("_http_status", ""),
        "http_error": payload.get("_http_error", ""),
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Phase 136 Entity Discovery QA",
        "",
        f"- Generated: {report['timestamp']}",
        f"- Mode: {report['mode']}",
        f"- Total: {report['total']}",
        f"- Passed: {report['passed']}",
        f"- Failed: {report['failed']}",
        f"- Pass rate: {report['pass_rate']:.3f}",
        "",
        "| Case | Intent | Entity | Mode | OK | Failed checks |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for row in report["results"]:
        failed = ", ".join(row.get("failed_checks") or []) or "-"
        lines.append(f"| `{row['id']}` | {row.get('intent') or '-'} | {row.get('entity_name') or '-'} | {row.get('answer_mode') or '-'} | {'yes' if row.get('ok') else 'no'} | {failed} |")
    return "\n".join(lines).strip() + "\n"


def write_report(report: Dict[str, Any], *, out_json: str | Path = DEFAULT_JSON, out_md: str | Path = DEFAULT_MD) -> None:
    Path(out_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    Path(out_md).write_text(render_markdown(report), encoding="utf-8")


def _execute_mock(case: EntityDiscoveryCase) -> Dict[str, Any]:
    entity_query = EntityIntentDetector().detect(case.query)
    source_plan = EntitySourcePlanner().plan(entity_query)
    evidence = _mock_evidence(entity_query)
    profiles = _mock_profiles(entity_query)
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
        "verified_strong_source": answer.mode == "verified_entity_fact",
        "public_only_policy": list(entity_query.public_only_policy),
    }
    return {"answer": answer.as_text(), "entity_intelligence_summary": summary, "trace": {"entity_intelligence_summary": summary}}


def _mock_evidence(entity_query) -> List[EntityEvidence]:
    if entity_query.intent in {EntityIntent.CEO_LOOKUP, EntityIntent.FOUNDER_LOOKUP}:
        return [
            EntityEvidence(title="Company LinkedIn public page", source_type="company_linkedin", candidate_name="Likely Candidate", supports_claim=True),
            EntityEvidence(title="Directory snippet", source_type="registry_directory", candidate_name="Likely Candidate", supports_claim=True),
        ]
    if entity_query.intent == EntityIntent.LEGITIMACY_CHECK:
        return [EntityEvidence(title="Official website", source_type="official_website", supports_claim=True)]
    return []


def _mock_profiles(entity_query) -> List[EntityProfileCandidate]:
    if entity_query.intent == EntityIntent.OFFICIAL_SOCIAL_PROFILE:
        return [EntityProfileCandidate(handle="@relyceinfotech", platform=entity_query.platform or "instagram", evidence_type="verified_social_link", linked_from_official_site=True, domain_match=True, name_match=0.96)]
    if entity_query.intent == EntityIntent.LINKEDIN_PROFILE:
        return [EntityProfileCandidate(handle="Relyce Infotech", platform="linkedin", evidence_type="company_linkedin", domain_match=True, name_match=0.9)]
    return []


def _execute_live(case: EntityDiscoveryCase, *, base_url: str, auth_token: str, timeout: float) -> Dict[str, Any]:
    token = auth_token or os.getenv("TAOS_LIVE_AUTH_TOKEN") or os.getenv("FIREBASE_ID_TOKEN") or ""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps({"query": case.query, "include_trace": True}).encode("utf-8")
    request = urllib.request.Request(f"{base_url.rstrip('/')}/execute", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
        return payload
    except Exception as exc:
        return {"answer": "", "_http_error": str(exc), "_http_status": 0}


def _summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    candidates = [
        payload.get("entity_intelligence_summary"),
        _nested(payload, "metadata", "entity_intelligence_summary"),
        _nested(payload, "trace", "entity_intelligence_summary"),
        _nested(payload, "trace", "universal_understanding", "entity_intelligence_summary"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _answer(payload: Dict[str, Any]) -> str:
    for key in ("answer", "response", "text", "error"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _private_policy_violation(payload: Dict[str, Any]) -> bool:
    text = json.dumps(payload, default=str).lower()
    return "bypass login" in text and "do not bypass login" not in text or "private_profile_used" in text


def _nested(value: Dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Phase 136 entity discovery QA")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--out-json", default=str(DEFAULT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_MD))
    args = parser.parse_args(argv)
    report = run_cases(load_cases(args.cases, max_cases=args.max_cases), live=bool(args.live), base_url=args.base_url, auth_token=args.auth_token)
    write_report(report, out_json=args.out_json, out_md=args.out_md)
    print(render_markdown(report))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
