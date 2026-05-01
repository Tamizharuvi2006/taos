"""Check route integrity telemetry in an intelligence eval report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


def _load_report(path: str | Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("Eval report must be a JSON object")
    return data


def _route_rows(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in list(report.get("results") or []):
        if not isinstance(item, dict):
            continue
        telemetry = item.get("route_telemetry")
        if not isinstance(telemetry, dict):
            continue
        row = dict(telemetry)
        row.setdefault("case_id", item.get("case_id"))
        rows.append(row)
    return rows


def evaluate_route_integrity(report: Dict[str, Any], *, max_mismatches: int = 0) -> tuple[List[str], Dict[str, Any]]:
    rows = _route_rows(report)
    if not rows:
        return ["No route telemetry rows found in eval report."], {"case_count": 0}

    mismatches = [row for row in rows if row.get("matched_expected") is False]
    failures: List[str] = []
    over_limit = len(mismatches) > int(max_mismatches)
    if over_limit:
        failures.append(
            f"route mismatch count {len(mismatches)} exceeds max {int(max_mismatches)}"
        )
        for row in mismatches:
            failures.append(
                "route mismatch: "
                f"{row.get('case_id')} expected={row.get('expected_type')} "
                f"observed={row.get('observed_type')} route={row.get('route_label')} owner={row.get('owner')}"
            )

    summary = {
        "case_count": len(rows),
        "route_match_rate": round((len(rows) - len(mismatches)) / float(max(1, len(rows))), 3),
        "route_mismatch_count": len(mismatches),
    }
    return failures, summary


def _mismatch_signature(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        str(row.get("case_id") or ""),
        str(row.get("expected_type") or ""),
        str(row.get("observed_type") or ""),
        str(row.get("route_label") or ""),
        str(row.get("owner") or ""),
    )


def _load_baseline_signatures(path: str | Path) -> Set[Tuple[str, str, str, str, str]]:
    data = _load_report(path)
    rows = list(data.get("mismatches") or [])
    signatures: Set[Tuple[str, str, str, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        signatures.add(
            (
                str(row.get("case_id") or ""),
                str(row.get("expected_type") or ""),
                str(row.get("observed_type") or ""),
                str(row.get("route_label") or ""),
                str(row.get("owner") or ""),
            )
        )
    return signatures


def evaluate_route_regression(
    report: Dict[str, Any],
    *,
    baseline_signatures: Set[Tuple[str, str, str, str, str]],
    max_new_mismatches: int = 0,
) -> List[str]:
    rows = _route_rows(report)
    mismatches = [row for row in rows if row.get("matched_expected") is False]
    current_signatures = {_mismatch_signature(row) for row in mismatches}
    new_signatures = sorted(current_signatures - baseline_signatures)

    failures: List[str] = []
    if len(new_signatures) > int(max_new_mismatches):
        failures.append(
            f"new route mismatch count {len(new_signatures)} exceeds max {int(max_new_mismatches)}"
        )
    for case_id, expected, observed, route_label, owner in new_signatures:
        failures.append(
            "new route mismatch: "
            f"{case_id} expected={expected} observed={observed} route={route_label} owner={owner}"
        )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Check eval route telemetry integrity.")
    parser.add_argument("report", help="Path to intelligence eval report JSON")
    parser.add_argument("--max-mismatches", type=int, default=0)
    parser.add_argument("--baseline", default="", help="Optional baseline mismatch JSON file.")
    parser.add_argument("--max-new-mismatches", type=int, default=0)
    args = parser.parse_args()

    report = _load_report(args.report)
    failures, summary = evaluate_route_integrity(
        report,
        max_mismatches=int(args.max_mismatches),
    )
    if str(args.baseline or "").strip():
        baseline_signatures = _load_baseline_signatures(args.baseline)
        failures.extend(
            evaluate_route_regression(
                report,
                baseline_signatures=baseline_signatures,
                max_new_mismatches=int(args.max_new_mismatches),
            )
        )
    print(f"route_case_count       : {summary.get('case_count', 0)}")
    print(f"route_match_rate       : {summary.get('route_match_rate', 0.0):.3f}")
    print(f"route_mismatch_count   : {summary.get('route_mismatch_count', 0)}")
    if failures:
        print("Route integrity failures:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("Route integrity check passed.")


if __name__ == "__main__":
    main()
