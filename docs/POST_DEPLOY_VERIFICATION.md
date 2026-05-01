# TAOS Post-deploy Verification (Phase 124)

## Goal
Validate production deployment health, safety, and rollback readiness after release execution.

## Immediate Checks (0-10 minutes)
1. Confirm backend `/health` returns `200`.
2. Confirm core auth endpoint behavior (`/users/me`) is healthy.
3. Confirm no raw internals or secret material in public error responses.
4. Confirm dashboard artifacts refresh succeeds.

## Smoke Verification
1. Run: `python scripts/run_deployment_smoke.py --live --base-url <production-or-staging-url>`
2. Stop and rollback if smoke fails.
3. Do not continue if `/health` is not `200`.

## Sample QA Verification
1. Run: `python scripts/run_full_live_qa_matrix.py --live --base-url <production-or-staging-url> --max-cases 5`
2. Validate route/contract checks on sampled cases.
3. Confirm package source-of-record behavior has not regressed.

## Ops Dashboard
1. Run: `python scripts/build_ops_dashboard.py`
2. Confirm `docs/ops_dashboard_latest.json` and `docs/ops_dashboard_latest.md` are updated.
3. Verify Super Admin dashboard panels are loading and current.

## Safety and Config Confirmation
- `AUTH_ALLOW_DEV_BYPASS=false`
- Dev perf bypass disabled in production.
- Production CORS policy confirmed.
- Provider and Firebase production keys confirmed.

## Rollback Readiness
1. Validate rollback plan exists: `docs/ROLLBACK_PLAN.md`
2. Confirm backend and frontend rollback procedures are executable.
3. Keep rollback command ownership and contact path ready.

## Release Completion Criteria
- Smoke live passed.
- Sample QA live passed.
- Dashboard refreshed.
- No release-blocking alerts.
- Rollback readiness confirmed.
