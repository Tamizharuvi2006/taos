import asyncio
import json
from taos.core.evaluation.intelligence_eval import load_intelligence_eval_cases, score_intelligence_response
from taos.orchestration.engine import OrchestrationEngine

TARGET = {'killer_15_followup_trap','killer_17_adversarial_formatting','killer_20_minimal_next'}
cases = [c for c in load_intelligence_eval_cases('D:/agent/taos/tests/fixtures/intelligence_eval_cases_v2.json') if c.case_id in TARGET]

async def run_case(case):
    e = OrchestrationEngine()
    e._settings.entity_lookup_v1_enabled = True
    uid = 'eval_' + case.case_id
    if getattr(case, 'context', ''):
        await e.run(goal=case.context, user_id=uid, include_trace=True)
    resp = await e.run(goal=case.query, user_id=uid, include_trace=True)
    if getattr(case, 'follow_up', ''):
        resp = await e.run(goal=case.follow_up, user_id=uid, include_trace=True)
    s = score_intelligence_response(case=case, response=resp)
    return {
        'case_id': case.case_id,
        'overall_score': s['overall_score'],
        'observed_type': s['observed_type'],
        'summary': s['summary'],
        'scores': s['scores'],
        'experimental_scores': s['experimental_scores'],
        'answer_preview': str(resp.get('formatted_response') or '')[:350],
        'trace_route_label': (resp.get('trace') or {}).get('route_label'),
        'trace_planner_path': (resp.get('trace') or {}).get('planner_path'),
        'trace_fallback_reason': (resp.get('trace') or {}).get('fallback_reason'),
    }

async def main():
    rows = []
    for c in cases:
        rows.append(await run_case(c))
    print(json.dumps(rows, indent=2))

asyncio.run(main())
