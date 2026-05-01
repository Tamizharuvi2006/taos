"""Quality gate checker for intelligence evaluation reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _load_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _get_metric(report: Dict[str, Any], name: str) -> float:
    if name in report:
        try:
            return float(report.get(name) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    metric_averages = report.get("metric_averages")
    if isinstance(metric_averages, dict):
        try:
            return float(metric_averages.get(name) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _get_failed_cases(report: Dict[str, Any]) -> int:
    for key in ("failed_cases", "failed_count", "failed"):
        if key in report:
            try:
                return int(report.get(key) or 0)
            except (TypeError, ValueError):
                return 0
    failures = report.get("failures")
    if isinstance(failures, list):
        return len(failures)
    return 0


def _check_min(
    metric_name: str,
    actual: float,
    minimum: float,
    issues: List[str],
) -> None:
    if actual < minimum:
        issues.append(f"{metric_name}: {actual:.3f} < {minimum:.3f}")


def _check_max(
    metric_name: str,
    actual: float,
    maximum: float,
    issues: List[str],
) -> None:
    if actual > maximum:
        issues.append(f"{metric_name}: {actual} > {maximum}")


def _compare_regression(
    current: Dict[str, Any],
    previous: Dict[str, Any],
    cfg: Dict[str, Any],
    issues: List[str],
) -> None:
    drop_checks = (
        ("overall_score", "overall_score_max_drop"),
        ("citation_quality", "citation_quality_max_drop"),
        ("confidence_calibration", "confidence_calibration_max_drop"),
        ("followup_usefulness", "followup_usefulness_max_drop"),
        ("mode_consistency", "mode_consistency_max_drop"),
    )
    for metric_name, cfg_key in drop_checks:
        if cfg_key not in cfg:
            continue
        drop = _get_metric(previous, metric_name) - _get_metric(current, metric_name)
        threshold = float(cfg[cfg_key])
        if drop > threshold:
            issues.append(
                f"{metric_name} regression: drop {drop:.3f} exceeds max {threshold:.3f}"
            )
    if "failed_cases_max_increase" in cfg:
        increase = _get_failed_cases(current) - _get_failed_cases(previous)
        threshold = int(cfg["failed_cases_max_increase"])
        if increase > threshold:
            issues.append(
                f"failed_cases regression: increase {increase} exceeds max {threshold}"
            )


def evaluate_report(
    report: Dict[str, Any],
    thresholds: Dict[str, Any],
    *,
    previous_report: Dict[str, Any] | None = None,
) -> Tuple[List[str], List[str]]:
    hard_fail_cfg = dict(thresholds.get("hard_fail") or {})
    warn_cfg = dict(thresholds.get("warn_only") or {})
    regression_hard_cfg = dict(thresholds.get("regression_hard") or {})
    regression_warn_cfg = dict(thresholds.get("regression_warn") or {})

    hard_failures: List[str] = []
    warnings: List[str] = []

    metric_min_keys = (
        "overall_score_min",
        "citation_quality_min",
        "confidence_calibration_min",
        "followup_usefulness_min",
        "mode_consistency_min",
        "uncertainty_honesty_min",
    )

    for key in metric_min_keys:
        if key in hard_fail_cfg:
            metric = key.replace("_min", "")
            _check_min(
                metric_name=metric,
                actual=_get_metric(report, metric),
                minimum=float(hard_fail_cfg[key]),
                issues=hard_failures,
            )
        if key in warn_cfg:
            metric = key.replace("_min", "")
            _check_min(
                metric_name=metric,
                actual=_get_metric(report, metric),
                minimum=float(warn_cfg[key]),
                issues=warnings,
            )

    if "failed_cases_max" in hard_fail_cfg:
        _check_max(
            metric_name="failed_cases",
            actual=_get_failed_cases(report),
            maximum=int(hard_fail_cfg["failed_cases_max"]),
            issues=hard_failures,
        )
    if "failed_cases_max" in warn_cfg:
        _check_max(
            metric_name="failed_cases",
            actual=_get_failed_cases(report),
            maximum=int(warn_cfg["failed_cases_max"]),
            issues=warnings,
        )

    if previous_report is not None:
        _compare_regression(current=report, previous=previous_report, cfg=regression_hard_cfg, issues=hard_failures)
        _compare_regression(current=report, previous=previous_report, cfg=regression_warn_cfg, issues=warnings)

    return hard_failures, warnings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check eval report against configured thresholds.")
    parser.add_argument("report", help="Path to current eval report JSON")
    parser.add_argument("thresholds", help="Path to threshold config JSON")
    parser.add_argument(
        "--previous-report",
        default="",
        help="Optional previous eval report JSON to enforce regression constraints.",
    )
    return parser.parse_args()


def _print_summary(report: Dict[str, Any], warnings: List[str], hard_failures: List[str]) -> None:
    print("=== EVAL SUMMARY ===")
    print(f"overall_score          : {_get_metric(report, 'overall_score'):.3f}")
    print(f"citation_quality       : {_get_metric(report, 'citation_quality'):.3f}")
    print(f"confidence_calibration : {_get_metric(report, 'confidence_calibration'):.3f}")
    print(f"followup_usefulness    : {_get_metric(report, 'followup_usefulness'):.3f}")
    print(f"mode_consistency       : {_get_metric(report, 'mode_consistency'):.3f}")
    print(f"uncertainty_honesty    : {_get_metric(report, 'uncertainty_honesty'):.3f}")
    print(f"failed_cases           : {_get_failed_cases(report)}")

    if warnings:
        print("\n=== WARNINGS ===")
        for msg in warnings:
            print(f"- {msg}")

    if hard_failures:
        print("\n=== HARD FAILURES ===")
        for msg in hard_failures:
            print(f"- {msg}")


def main() -> int:
    args = _parse_args()
    report = _load_json(args.report)
    thresholds = _load_json(args.thresholds)
    previous_report = _load_json(args.previous_report) if str(args.previous_report).strip() else None

    hard_failures, warnings = evaluate_report(
        report=report,
        thresholds=thresholds,
        previous_report=previous_report,
    )
    _print_summary(report=report, warnings=warnings, hard_failures=hard_failures)

    if hard_failures:
        return 1
    print("\nEVAL STATUS: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
