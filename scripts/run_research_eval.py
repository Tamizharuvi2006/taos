from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_PARENT = _REPO_ROOT.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))

from taos.core.evaluation.live_eval_guard import LiveEvalGuard
from taos.core.evaluation.research_eval import ResearchEvalCase, ResearchEvalHarness


class MockResearchRunner:
    async def run_case(self, case: ResearchEvalCase) -> Dict[str, Any]:
        route = case.expected_route
        if case.category == "failure" and not case.expects_weak_or_no_evidence:
            return {
                "route": route,
                "mode": route,
                "answer_mode": "no_usable_evidence",
                "answer": "I couldn't verify this from reliable sources.",
                "confidence": 0.18,
                "sources": [],
                "warnings": ["No reliable evidence found."],
                "trust_block": {
                    "citation_coverage": 0.0,
                    "supported_claims": 0,
                    "unsupported_claims": 0,
                    "freshness_summary": {"freshness_score": 0.0, "stale_detected": False},
                    "extraction_recovery_used": False,
                    "answer_mode": "no_usable_evidence",
                },
            }
        min_sources = max(1, int(case.min_sources or 1))
        sources = [f"https://source{i}.example.com/{case.id}" for i in range(1, min_sources + 1)]
        answer_mode = "weak_candidate" if case.expects_weak_or_no_evidence else "best_supported"
        answer = (
            "Answer\n"
            f"The best-supported answer is: mock evidence supports a grounded response for {case.query}. [S1]\n\n"
            "Why this answer\n"
            "- S1 gives the strongest direct support.\n"
            "- The remaining sources provide corroborating context.\n\n"
            "Confidence\n"
            "Medium, because this mock gate checks route, evidence, coverage, and answer utility.\n\n"
            "What to treat carefully\n"
            "- Treat live provider drift as unknown in mock mode.\n\n"
            "Sources\n"
            "- S1 mock source."
        )
        for snippet in case.expected_behavior[:2]:
            answer += f" {snippet}"
        evidence_stats = {
            "answer_mode": answer_mode,
            "usable_sources_count": len(sources),
            "official_source_count": 1 if case.requires_official_source else 0,
            "trusted_source_count": max(0, len(sources) - 1),
            "extract_attempted_count": len(sources),
            "extract_success_count": len(sources),
            "coverage": 0.82,
            "unsupported_critical_claims": 0,
            "freshness_mode": "high" if case.freshness_required else "normal",
            "conflict_detected": bool(case.requires_conflict_handling),
            "conflict_summary": {
                "conflict_detected": bool(case.requires_conflict_handling),
                "groups": [{"claim": "mock disputed ranking", "sources_for": ["S1"], "sources_against": ["S2"]}]
                if case.requires_conflict_handling
                else [],
            },
            "answer_policy": {"answer_mode": answer_mode},
        }
        return {
            "route": route,
            "mode": route,
            "answer_mode": answer_mode,
            "answer": answer,
            "confidence": 0.72 if route != "deep_search" else 0.67,
            "sources": sources,
            "warnings": [],
            "evidence_matrix_summary": {
                "citation_coverage": 0.82 if route != "fast_search" else 0.66,
                "supported_claims": 3,
                "unsupported_claims": 0,
            },
            "freshness_summary": {
                "freshness_score": 0.88 if case.freshness_required else 0.75,
                "stale_detected": False,
                "freshness_mode": "high" if case.freshness_required else "normal",
            },
            "extraction_recovery_used": False,
            "metadata": {"evidence_stats": evidence_stats},
            "trust_block": {
                "citation_coverage": 0.82 if route != "fast_search" else 0.66,
                "supported_claims": 3,
                "unsupported_claims": 0,
                "unsupported_critical_claims": 0,
                "source_diversity_score": min(1.0, len(sources) / max(1, min_sources)),
                "answer_mode": answer_mode,
                "official_source_count": evidence_stats["official_source_count"],
                "trusted_source_count": evidence_stats["trusted_source_count"],
                "freshness_summary": {
                    "freshness_score": 0.88 if case.freshness_required else 0.75,
                    "stale_detected": False,
                    "freshness_mode": "high" if case.freshness_required else "normal",
                },
                "conflict_summary": evidence_stats["conflict_summary"],
            },
        }


class LiveResearchRunner:
    def __init__(self) -> None:
        from taos.orchestration.engine import OrchestrationEngine

        self._engine = OrchestrationEngine()

    async def run_case(self, case: ResearchEvalCase) -> Dict[str, Any]:
        return await self._engine.run(
            case.query,
            request_id=f"live_eval_{case.id}",
            user_id="eval",
            include_trace=True,
        )


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Run TAOS research evaluation harness")
    parser.add_argument("--cases", default="eval/research_cases.json")
    parser.add_argument("--report", default="eval/research_eval_report.md")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--mock", action="store_true", help="Run with mocked outputs only")
    parser.add_argument("--live", action="store_true", help="Run with real providers when environment is ready")
    parser.add_argument("--max-cases", type=int, default=8)
    parser.add_argument("--max-cost", type=float, default=1.0)
    parser.add_argument("--max-runtime", type=float, default=300.0)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    cases_path = (repo_root / args.cases).resolve()
    report_path = (repo_root / args.report).resolve()
    json_path = (repo_root / args.json_out).resolve() if args.json_out else None

    harness = ResearchEvalHarness()
    cases = harness.load_cases(cases_path)
    use_live = bool(args.live and not args.mock)
    guard = LiveEvalGuard(
        max_cases=args.max_cases,
        max_cost_estimate=args.max_cost,
        max_runtime_seconds=args.max_runtime,
        required_env=["SERPER_API_KEY", "OPENROUTER_API_KEY"],
    )
    readiness = guard.readiness(live_requested=use_live)
    if use_live and not readiness["ready"]:
        evaluation = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results": [],
            "aggregates": {
                "route_accuracy": 0.0,
                "groundedness_score": 0.0,
                "citation_quality": 0.0,
                "source_diversity": 0.0,
                "freshness_score": 0.0,
                "confidence_calibration": 0.0,
                "hallucination_resistance": 0.0,
                "latency_score": 0.0,
                "overall_score": 0.0,
                "cases_run": 0,
                "failed_cases": [],
                "weak_areas": ["Live evaluation skipped because provider env vars are missing."],
                "recommendations": ["Set required provider env vars, then rerun with --live."],
            },
            "live_guard": {"readiness": readiness, **guard.summary()},
        }
    else:
        runner = LiveResearchRunner() if use_live else MockResearchRunner()
        selected_cases = cases
        if use_live:
            selected_cases = []
            for case in cases:
                if case.is_package_version_case:
                    guard.skip_case(case.id, "package_version_excluded")
                    continue
                check = guard.before_case(case.id)
                if check.get("allowed"):
                    selected_cases.append(case)
            if not selected_cases:
                selected_cases = []
        evaluation = await harness.evaluate_cases(cases=selected_cases, runner=runner)
        evaluation["live_guard"] = {"readiness": readiness, **guard.summary(), "live_mode": use_live}
    markdown = harness.render_markdown_report(evaluation)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    if use_live:
        live_dir = repo_root / "eval" / "live_runs"
        live_dir.mkdir(parents=True, exist_ok=True)
        report_path, default_json_path = _live_report_paths(repo_root)
        if not json_path:
            json_path = default_json_path
        evaluation["live_report_path"] = str(report_path)
        evaluation["live_json_path"] = str(json_path)
    report_path.write_text(markdown, encoding="utf-8")
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(evaluation, indent=2), encoding="utf-8")

    print(markdown)
    return 0


def _live_report_paths(repo_root: Path, *, stamp: str | None = None) -> tuple[Path, Path]:
    live_dir = repo_root / "eval" / "live_runs"
    live_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return live_dir / f"research_eval_{stamp}.md", live_dir / f"research_eval_{stamp}.json"


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
