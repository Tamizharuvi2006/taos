"""Run Phase-91 intelligence refinement evaluation benchmark against TAOS API."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List

import httpx

from taos.core.evaluation.intelligence_eval import (
    EXPERIMENTAL_METRIC_NAMES,
    METRIC_NAMES,
    build_tuning_recommendations,
    load_intelligence_eval_cases,
    score_intelligence_response,
    summarize_experimental_metric_averages,
    summarize_metric_averages,
    summarize_route_telemetry,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run intelligence consistency benchmark.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="TAOS API base URL")
    parser.add_argument(
        "--cases",
        default="tests/fixtures/intelligence_eval_cases.json",
        help="Path to benchmark case fixture JSON",
    )
    parser.add_argument("--user-id", default="phase91_eval_user", help="User id passed to execute endpoint")
    parser.add_argument(
        "--auth-token",
        default="",
        help="Optional bearer token for protected environments.",
    )
    parser.add_argument(
        "--use-bypass-header",
        action="store_true",
        help="Send X-User-ID header for development auth bypass.",
    )
    parser.add_argument("--timeout", type=float, default=90.0, help="Per-request timeout seconds")
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Start index for case batching (0-based).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of cases to run from offset. 0 means all remaining cases.",
    )
    parser.add_argument(
        "--out",
        default="docs/intelligence_eval_latest.json",
        help="Output report JSON path",
    )
    parser.add_argument(
        "--include-trace",
        action="store_true",
        default=True,
        help="Include trace in execute response (enabled by default)",
    )
    return parser.parse_args()


def _call_execute(
    base_url: str,
    query: str,
    user_id: str,
    timeout: float,
    include_trace: bool,
    auth_token: str = "",
    use_bypass_header: bool = False,
) -> Dict[str, Any]:
    payload = {
        # Keep both keys for compatibility across API schema variants.
        "goal": query,
        "query": query,
        "user_id": user_id,
        "include_trace": bool(include_trace),
    }
    headers: Dict[str, str] = {}
    token = str(auth_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if use_bypass_header:
        headers["X-User-ID"] = user_id
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/execute",
            json=payload,
            headers=headers if headers else None,
        )
        response.raise_for_status()
        return response.json()


def _run_case_with_optional_context_and_follow_up(
    *,
    base_url: str,
    case: Any,
    user_id: str,
    timeout: float,
    include_trace: bool,
    auth_token: str,
    use_bypass_header: bool,
) -> Dict[str, Any]:
    turns: List[Dict[str, Any]] = []

    if getattr(case, "context", ""):
        _call_execute(
            base_url=base_url,
            query=case.context,
            user_id=user_id,
            timeout=timeout,
            include_trace=include_trace,
            auth_token=auth_token,
            use_bypass_header=use_bypass_header,
        )
        turns.append({"type": "context", "query": case.context})

    primary_response = _call_execute(
        base_url=base_url,
        query=case.query,
        user_id=user_id,
        timeout=timeout,
        include_trace=include_trace,
        auth_token=auth_token,
        use_bypass_header=use_bypass_header,
    )
    turns.append({"type": "query", "query": case.query})
    scored_response = primary_response
    scored_turn = "query"

    if getattr(case, "follow_up", ""):
        follow_up_response = _call_execute(
            base_url=base_url,
            query=case.follow_up,
            user_id=user_id,
            timeout=timeout,
            include_trace=include_trace,
            auth_token=auth_token,
            use_bypass_header=use_bypass_header,
        )
        turns.append({"type": "follow_up", "query": case.follow_up})
        scored_response = follow_up_response
        scored_turn = "follow_up"

    return {
        "response": scored_response,
        "turns": turns,
        "scored_turn": scored_turn,
    }


def main() -> None:
    args = _parse_args()
    all_cases = load_intelligence_eval_cases(args.cases)
    if not all_cases:
        raise RuntimeError("No intelligence evaluation cases loaded.")
    offset = max(0, int(args.offset))
    limit = int(args.limit)
    cases = all_cases[offset:]
    if limit > 0:
        cases = cases[:limit]
    if not cases:
        raise RuntimeError(
            f"No cases selected after batching. total={len(all_cases)} offset={offset} limit={limit}"
        )

    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for case in cases:
        try:
            case_run = _run_case_with_optional_context_and_follow_up(
                base_url=args.base_url,
                case=case,
                user_id=args.user_id,
                timeout=float(args.timeout),
                include_trace=bool(args.include_trace),
                auth_token=args.auth_token,
                use_bypass_header=bool(args.use_bypass_header),
            )
            scored = score_intelligence_response(case=case, response=case_run["response"])
            scored["status"] = "ok"
            scored["turns"] = list(case_run["turns"])
            scored["scored_turn"] = str(case_run["scored_turn"])
            results.append(scored)
            print(
                f"[OK] {case.case_id}: overall={scored['overall_score']:.3f} "
                f"mode={scored['observed_type']} ({scored['summary']}) "
                f"turn={scored['scored_turn']}"
            )
        except Exception as exc:
            failures.append(
                {
                    "case_id": case.case_id,
                    "query": case.query,
                    "follow_up": getattr(case, "follow_up", ""),
                    "context": getattr(case, "context", ""),
                    "error": str(exc),
                }
            )
            print(f"[FAIL] {case.case_id}: {exc}")

    metric_averages = summarize_metric_averages(results)
    experimental_metric_averages = summarize_experimental_metric_averages(results)
    route_telemetry = summarize_route_telemetry(results)
    overall = round(mean([row["overall_score"] for row in results]), 3) if results else 0.0
    recommendations = build_tuning_recommendations(results)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "total_fixture_cases": len(all_cases),
        "batch_offset": offset,
        "batch_limit": limit,
        "case_count": len(cases),
        "completed_count": len(results),
        "failed_count": len(failures),
        "completed": len(results),
        "failed": len(failures),
        "overall_score": overall,
        "metric_averages": metric_averages,
        "experimental_metric_averages": experimental_metric_averages,
        "route_telemetry": route_telemetry,
        "recommendations": recommendations,
        "metrics": METRIC_NAMES,
        "experimental_metrics": EXPERIMENTAL_METRIC_NAMES,
        "results": results,
        "failures": failures,
    }
    for metric in METRIC_NAMES:
        report[metric] = float(metric_averages.get(metric, 0.0) or 0.0)
    for metric in EXPERIMENTAL_METRIC_NAMES:
        report[f"experimental_{metric}"] = float(experimental_metric_averages.get(metric, 0.0) or 0.0)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\nSaved report to: {out_path}")
    print(f"Overall score: {overall:.3f}")
    print(
        "Route match: "
        f"{route_telemetry.get('route_match_rate', 0.0):.3f} "
        f"({route_telemetry.get('route_mismatch_count', 0)} mismatches)"
    )
    print("Experimental metric averages (log-only):")
    for metric in EXPERIMENTAL_METRIC_NAMES:
        print(f"  - {metric}: {experimental_metric_averages.get(metric, 0.0):.3f}")
    print("Top recommendations:")
    for idx, rec in enumerate(recommendations, start=1):
        print(f"  {idx}. {rec}")


if __name__ == "__main__":
    main()
