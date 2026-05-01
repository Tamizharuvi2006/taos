from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_PARENT = _REPO_ROOT.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))

from taos.core.answering.research_answer_composer_v2 import ResearchAnswerComposerV2
from taos.core.search.query_planner_v2 import SearchQueryPlannerV2
from taos.core.understanding.global_query_normalizer import GlobalQueryNormalizer


def load_cases(path: str | Path) -> List[Dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_cases(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    planner = SearchQueryPlannerV2()
    global_normalizer = GlobalQueryNormalizer()
    composer = ResearchAnswerComposerV2()
    results: List[Dict[str, Any]] = []
    for case in cases:
        query = str(case.get("query") or "")
        normalized = global_normalizer.normalize(query)
        plan = planner.plan(str(normalized.get("normalized_text") or query))
        flattened = plan.flatten()
        answer = composer.compose(
            query=query,
            intent=plan.intent,
            status="not_confirmed" if plan.intent == "rumour_verification" else "",
            best_supported="The best-supported answer is based on the normalized intent and source-aware plan.",
            evidence_rows=[{"title": "Mock related evidence"}],
            answer_mode="best_supported",
        )
        failed: List[str] = []
        if case.get("expected_intent") and plan.intent != case["expected_intent"]:
            failed.append("intent_mismatch")
        if case.get("forbid_primary_raw_query") and flattened and flattened[0].lower() == query.lower():
            failed.append("raw_query_primary")
        if case.get("forbid_generic_no_result") and "could not verify" in answer.lower():
            failed.append("generic_no_result")
        if case.get("requires_official") and not plan.lane("official").queries:
            failed.append("missing_official_lane")
        results.append(
            {
                "id": case.get("id"),
                "query": query,
                "intent": plan.intent,
                "query_plan": plan.summary(),
                "answer": answer,
                "failed_checks": failed,
                "ok": not failed,
            }
        )
    passed = sum(1 for row in results if row["ok"])
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": passed / max(1, len(results)),
        "results": results,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Research Killer Eval v2",
        "",
        f"- Generated: {report['timestamp']}",
        f"- Pass rate: {report['pass_rate']:.3f}",
        f"- Passed: {report['passed']}",
        f"- Failed: {report['failed']}",
        "",
        "| Case | Intent | OK | Failed checks |",
        "| --- | --- | ---: | --- |",
    ]
    for row in report["results"]:
        failed = ", ".join(row.get("failed_checks") or []) or "-"
        lines.append(f"| `{row['id']}` | {row['intent']} | {'yes' if row['ok'] else 'no'} | {failed} |")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TAOS research killer eval v2")
    parser.add_argument("--cases", default="eval/research_killer_cases_v2.json")
    parser.add_argument("--out-json", default="eval/research_killer_eval_v2.json")
    parser.add_argument("--out-md", default="eval/research_killer_eval_v2.md")
    args = parser.parse_args()
    report = run_cases(load_cases(_REPO_ROOT / args.cases))
    (_REPO_ROOT / args.out_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (_REPO_ROOT / args.out_md).write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
