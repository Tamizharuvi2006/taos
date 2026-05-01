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
    "killer_04_high_stakes_weak_signal",
    "killer_07_casual_to_technical",
    "killer_09_research_plus_opinion",
}


async def main() -> None:
    cases = [c for c in load_intelligence_eval_cases("tests/fixtures/intelligence_eval_cases_v2.json") if c.case_id in TARGET]
    rows = []
    for case in cases:
        engine = OrchestrationEngine()
        response = await engine.run(goal=case.query, user_id=f"dbg_{case.case_id}", include_trace=True)
        scored = score_intelligence_response(case=case, response=response)
        rows.append(
            {
                "case_id": case.case_id,
                "overall_score": scored["overall_score"],
                "tier": scored["summary"],
                "mode": scored["observed_type"],
                "planner_path": (response.get("trace") or {}).get("planner_path"),
                "route_label": (response.get("trace") or {}).get("route_label"),
                "scores": scored["scores"],
                "preview": str(response.get("formatted_response") or "")[:1200],
            }
        )
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
