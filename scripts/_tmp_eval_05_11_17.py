import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from taos.core.evaluation.intelligence_eval import load_intelligence_eval_cases, score_intelligence_response
from taos.orchestration.engine import OrchestrationEngine

TARGET = {
    "killer_05_conflicting_info_trap",
    "killer_11_fake_authority_pressure",
    "killer_17_adversarial_formatting",
}


async def run_case(case):
    engine = OrchestrationEngine()
    uid = f"phase952_tune_{case.case_id}"
    response = await engine.run(goal=case.query, user_id=uid, include_trace=True)
    scored = score_intelligence_response(case=case, response=response)
    return {
        "case_id": case.case_id,
        "overall_score": scored["overall_score"],
        "tier": scored["summary"],
        "scores": scored["scores"],
        "experimental_scores": scored["experimental_scores"],
        "observed_type": scored["observed_type"],
        "planner_path": (response.get("trace") or {}).get("planner_path"),
        "route_label": (response.get("trace") or {}).get("route_label"),
        "preview": str(response.get("formatted_response") or "")[:1200],
    }


async def main():
    cases = [
        c
        for c in load_intelligence_eval_cases("tests/fixtures/intelligence_eval_cases_v2.json")
        if c.case_id in TARGET
    ]
    rows = []
    for case in cases:
        rows.append(await run_case(case))
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
