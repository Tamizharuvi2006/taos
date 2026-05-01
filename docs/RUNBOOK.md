# TAOS Real Live Runbook

## Purpose
This runbook defines the repeatable TAOS release flow for local, staging, and production-like verification.
It is operations-only and does not introduce new behavior.

## Pre-release Environment Check
- Confirm backend env is production-safe:
  - `TAOS_ENV`
  - `OPENROUTER_API_KEY`
  - `SERPER_API_KEY`
  - `STORAGE_BACKEND`
  - `FIREBASE_PROJECT_ID` / `FIREBASE_CLIENT_EMAIL` / `FIREBASE_PRIVATE_KEY` (when Firebase storage is enabled)
  - `AUTH_ALLOW_DEV_BYPASS` must be `false` outside local development smoke validation
- Run preflight:
  - `python scripts/release_preflight.py --mock`
- If plain `python` is unavailable on this machine, use Python 3.13 directly (for example `py -3.13 ...`).

## Backend Start/Stop Runbook
- Start backend:
  - `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- Verify health:
  - `python scripts/run_deployment_smoke.py --live --base-url http://localhost:8000 --dev-user-id <id>`
- Stop backend:
  - Stop the running `uvicorn` process from terminal/process manager.
- After stop, confirm API is down:
  - `python scripts/run_deployment_smoke.py --live --base-url http://localhost:8000 --dev-user-id <id>`
  - Expect blocked connectivity check.

## Frontend Build/Deploy Runbook
- Build frontend from the paired TAOS frontend workspace:
  - `cd D:\agent\frontend`
  - `npm run build`
- If build fails due environment-only issues (`spawn EPERM`, local worker limits), capture it as a known warning and continue backend verification.

## Smoke Test Runbook
- Mock smoke:
  - `python scripts/run_deployment_smoke.py --mock`
- Live smoke:
  - `python scripts/run_deployment_smoke.py --live --base-url http://localhost:8000 --dev-user-id <id>`

## Live QA Runbook
- Full QA mock:
  - `python scripts/run_full_live_qa_matrix.py --mock`
- Full QA live:
  - `python scripts/run_full_live_qa_matrix.py --live --base-url http://localhost:8000 --max-cases 8`

## Live Document QA Runbook
- Document QA mock:
  - `python scripts/run_document_live_qa.py --mock`
- Document QA live:
  - `python scripts/run_document_live_qa.py --live --base-url http://localhost:8000 --max-cases 3`
- Optional fixture skip behavior:
  - Live document cases may be skipped if fixture documents are not uploaded in the running server.
  - Skip is non-blocking when skip reason is fixture unavailability.

## Ops Dashboard Generation
- Build dashboard:
  - `python scripts/build_ops_dashboard.py`

## Security Checks
- Run research-eval safety gate in mock mode:
  - `python scripts/run_research_eval.py --mock`
- Verify deployment smoke keeps sanitized trace and production-safe error shape.
- Confirm admin routes remain protected (`/admin/super/ping`).

## Rollback Steps
- Follow:
  - `docs/ROLLBACK_PLAN.md`
- Always perform rollback verification smoke + full QA after rollback.

## Incident Response Steps
- Follow:
  - `docs/INCIDENT_RESPONSE.md`
- Log incident timeline, impact, mitigations, and rollback/recovery state.

## Release Sign-off
- Complete all checks in:
  - `docs/RELEASE_CHECKLIST.md`
- Record sign-off owner, timestamp, environment, and artifact links.
