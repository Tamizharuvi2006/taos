import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from taos.core.evaluation.intelligence_eval import load_intelligence_eval_cases, score_intelligence_response
from taos.orchestration.engine import OrchestrationEngine


async def run_case(case):
    engine = OrchestrationEngine()
    response = await engine.run(goal=case.query, user_id=f"phase952_sum_{case.case_id}", include_trace=True)
    scored = score_intelligence_response(case=case, response=response)
    return {
        "case_id": case.case_id,
        "overall_score": scored["overall_score"],
        "tier": scored["summary"],
    }


async def main():
    cases = [c for c in load_intelligence_eval_cases("tests/fixtures/intelligence_eval_cases_v2.json") if c.case_id.startswith("killer_")]
    rows = []
    for case in cases:
        rows.append(await run_case(case))
    rows.sort(key=lambda x: x["case_id"])
    done = [r for r in rows if r["tier"] in {"good", "excellent"}]
    improve = [r for r in rows if r["tier"] == "needs_tuning"]
    payload = {
        "done_count": len(done),
        "improve_count": len(improve),
        "done": done,
        "improve": improve,
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
