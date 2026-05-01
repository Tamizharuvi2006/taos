# Codex Live Handoff - 2026-05-01

## Purpose
Use this file first before opening the app browser or re-running long discovery work.

It captures:
- the exact local runtime that was already made to work
- the commands to boot frontend and backend fast
- the current Phase 146/146A/146B evidence status
- the known live blocker
- the exact files and commands to inspect next

Goal: reduce wasted browser retries, token burn, and duplicate environment setup.

## Workspace Layout
- Backend repo: `D:\agent\taos`
- Frontend repo: `D:\agent\frontend`
- Local venv already created: `D:\agent\taos\.venv`

## Current Local Runtime
- Frontend URL: `http://localhost:3000/chat`
- Frontend binds to: `127.0.0.1:3000`
- Backend base URL: `http://127.0.0.1:8000`
- Backend health endpoint: `http://127.0.0.1:8000/health`

## Important Env Flags
These mattered in the working local setup:

- `PYTHONPATH=D:\agent`
- `AUTH_ALLOW_DEV_BYPASS=true`
- `ENTITY_LOOKUP_V1_ENABLED=true`
- `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000`

## One-Time Setup
If the environment is missing dependencies:

```powershell
cd D:\agent\taos
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Fast Start Commands
Prefer these exact commands instead of rediscovering the boot path.

### Backend
```cmd
set PYTHONPATH=D:\agent&& set AUTH_ALLOW_DEV_BYPASS=true&& set ENTITY_LOOKUP_V1_ENABLED=true&& cd /d D:\agent\taos&& D:\agent\taos\.venv\Scripts\python.exe -m uvicorn taos.apps.api.main:app --host 127.0.0.1 --port 8000
```

### Frontend
```cmd
set NEXT_PUBLIC_API_URL=http://127.0.0.1:8000&& cd /d D:\agent\frontend&& npm.cmd run dev -- --hostname 127.0.0.1 --port 3000
```

## Quick Smoke Checks
Run these before spending time in the browser:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -ExpandProperty Content
```

```powershell
Invoke-WebRequest http://127.0.0.1:3000 | Select-Object -ExpandProperty StatusCode
```

## Browser Rule
Do not start by repeatedly testing in the browser.

Use this order:
1. read this file
2. start backend
3. start frontend
4. run the direct QA commands below
5. only then open `http://localhost:3000/chat`

## Most Relevant Files First
Read these before touching unrelated modules:

- `D:\agent\taos\scripts\run_live_evidence_qa.py`
- `D:\agent\taos\qa\live_evidence_cases.json`
- `D:\agent\taos\tests\test_phase146_live_provider_evidence_qa.py`
- `D:\agent\taos\tests\test_phase146a_entity_search_precision.py`
- `D:\agent\taos\tests\test_phase146b_live_provider_retrieval_fix.py`
- `D:\agent\taos\orchestration\engine.py`
- `D:\agent\taos\DEVELOPMENT_LOG.md`

## What Was Already Proven

### Phase 145
- CEO/founder now requires exact role proof.
- Employee/member evidence cannot verify CEO/founder.
- Weak snippets cannot verify sensitive role claims.
- Role mismatch is explicit in trust metadata.

### Phase 146
- Added the live/mock evidence QA runner.
- Mock QA passes.
- Live QA exposed real provider truth gaps.

### Phase 146A
- Entity query planning now includes exact LinkedIn/company/post CEO/founder lanes.
- Company match and target entity match signals were added.
- Unrelated Facebook/social noise is downgraded.
- Founder and team-member role extraction is stricter.

### Phase 146B
- Direct engine path now finds `Ukenthiran A` as the best-supported public CEO/founder candidate for Relyce from LinkedIn/company evidence.
- Unrelated Facebook noise is rejected in the patched direct path.
- Full live HTTP/API path still does not surface that patched entity result end-to-end.

## Current Truth About The Main Bug
The important gap is now narrow:

- entity policy/ranking logic is much safer
- direct engine execution can produce the better Relyce answer
- the live HTTP/API response path still falls back to the older generic unverified output

So the next fix is not another broad search-policy rewrite.

The next fix is:
- live API/runtime handoff
- preserving patched entity metadata in the API response
- ensuring the entity path used in direct engine execution is the same one surfaced by the live HTTP path

## Current Known Good Relyce CEO Outcome
When the patched direct engine path is used, the expected answer shape is:

- selected candidate: `Ukenthiran A`
- supported role: `founder_ceo`
- source type: `company_linkedin`
- confidence: candidate / LinkedIn-supported
- `Tamizharuvi p` stays team member / employee, not CEO
- unrelated Facebook results are rejected

Important:
- this is still not registry/source-of-record verified unless stronger official evidence exists

## Current Known Live Blocker
Even after backend restart with `ENTITY_LOOKUP_V1_ENABLED=true`, the full live HTTP QA still did not reflect the patched entity path.

Symptoms:
- generic unverified CEO/founder answers still appear from the API
- strict entity metadata is missing in live output
- LinkedIn/company candidate evidence is not consistently visible in live API answers
- profile/legitimacy prompts still drift into `official_search` / generic research behavior

## Exact Commands To Reproduce Current QA State

### Mock Evidence QA
```powershell
cd D:\agent\taos
D:\agent\taos\.venv\Scripts\python.exe scripts\run_live_evidence_qa.py --mock
```

### Live Evidence QA
```powershell
cd D:\agent\taos
$env:PYTHONPATH='D:\agent'
$env:PYTHONIOENCODING='utf-8'
D:\agent\taos\.venv\Scripts\python.exe scripts\run_live_evidence_qa.py --live --base-url http://127.0.0.1:8000 --max-cases 10
```

### Focused Regression Tests
```powershell
cd D:\agent\taos
python -m pytest -q tests/test_phase146b_live_provider_retrieval_fix.py
python -m pytest -q tests/test_phase146_live_provider_evidence_qa.py tests/test_phase146a_entity_search_precision.py
python -m pytest -q tests/test_phase145_search_depth_correctness.py tests/test_phase142_entity_real_provider_integration.py tests/test_phase95_entity_lookup.py
python -m pytest -q tests/test_phase107_deterministic_routing.py tests/test_phase140_trace_route_labels.py
```

## Artifacts Already Written
These files already capture the recent QA outputs:

- `D:\agent\taos\QA_RESULTS_LIVE_EVIDENCE.json`
- `D:\agent\taos\QA_RESULTS_LIVE_EVIDENCE.md`
- `D:\agent\taos\QA_RESULTS_ENTITY_DISCOVERY.json`
- `D:\agent\taos\QA_RESULTS_ENTITY_DISCOVERY.md`
- `D:\agent\taos\tmp_backend_live.out.log`
- `D:\agent\taos\tmp_backend_live.err.log`
- `D:\agent\taos\tmp_frontend_live.out.log`
- `D:\agent\taos\tmp_frontend_live.err.log`

## Recommended Next Debug Order
If a future Codex run is continuing from here, do this in order:

1. confirm backend health
2. run `scripts/run_live_evidence_qa.py --live --base-url http://127.0.0.1:8000 --max-cases 10`
3. compare the live QA output with the direct engine expectation in Phase 146B
4. inspect the live API response contract for missing:
   - `requested_role`
   - `supported_role`
   - `search_lanes_used`
   - `source_tiers_found`
   - `entity_answer_mode`
   - patched LinkedIn/company candidate evidence
5. fix the HTTP/API handoff before touching broader search strategy again

## What Not To Waste Time Rechecking First
- do not restart with new route logic unless a route regression is proven
- do not redo persistence or performance work for this issue
- do not weaken Phase 145 role verification
- do not hardcode `Ukenthiran A`; the fix must come from the evidence path

## Short Summary For The Next Codex
If you only read one paragraph:

The environment is already bootable locally on `3000` + `8000`, the entity policy layer is safer, and the direct engine path can now identify `Ukenthiran A` from LinkedIn/company evidence while keeping `Tamizharuvi` as team member only. The remaining bug is that the live HTTP/API path still returns the old generic unverified entity output and drops the patched metadata/evidence. Start from live QA and the API handoff, not from routing, persistence, or performance.
