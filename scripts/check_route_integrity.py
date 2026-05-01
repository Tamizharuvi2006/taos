"""Check route integrity telemetry in an intelligence eval report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


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
    if len(mismatches) > int(max_mismatches):
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Check eval route telemetry integrity.")
    parser.add_argument("report", help="Path to intelligence eval report JSON")
    parser.add_argument("--max-mismatches", type=int, default=0)
    args = parser.parse_args()

    failures, summary = evaluate_route_integrity(
        _load_report(args.report),
        max_mismatches=int(args.max_mismatches),
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
