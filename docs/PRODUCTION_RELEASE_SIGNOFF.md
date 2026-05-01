# TAOS Production Release Sign-off (Phase 124)

## Status
- Phase: `124`
- Date: `2026-04-26`
- RC status: `READY-for-signoff`
- Scope guard: release execution only, no feature work.

## Mandatory Preconditions
- [x] `docs/RC_FREEZE_CHECKLIST.md` is green.
- [x] `docs/RC_RELEASE_PACKAGE.md` indicates READY-for-signoff.
- [x] Performance reliability c1/c2/c5 live artifacts are passing.
- [x] Full live QA matrix artifact exists and is passing.
- [x] Deployment smoke artifact exists and is passing.
- [x] Phase 118 security regression tests are passing.
- [x] Frontend build command is defined and verified.
- [x] Rollback plan exists (`docs/ROLLBACK_PLAN.md`).
- [x] Known warnings documented (`docs/KNOWN_WARNINGS.md`).
- [ ] Release owner sign-off recorded.
- [ ] Reviewer sign-off recorded.

## Production Safety Checklist
- [x] `TAOS_ENV=production`
- [x] `AUTH_ALLOW_DEV_BYPASS=false`
- [x] Do not run dev perf bypass in production.
- [x] `PERF_TEST_MODE` disabled for production release execution.
- [x] CORS policy is production-safe (no wildcard for privileged routes).
- [x] Provider keys present (`OPENROUTER_API_KEY`, `SERPER_API_KEY`).
- [x] Firebase production project configuration verified.
- [x] Public API responses keep safe error shape (no raw trace internals).

## Deployment Execution Plan
1. Confirm production environment values and secrets.
2. Confirm dev bypass is disabled.
3. Confirm production CORS policy.
4. Confirm provider + Firebase keys.
5. Build frontend:
   `cd D:\agent\frontend`
   `npm run build`
6. Deploy backend.
7. Deploy frontend.
8. Verify `/health`.
9. Run deployment smoke (live).
10. Run limited full QA sample (live, max-cases 5).
11. Refresh ops dashboard.
12. Verify Super Admin Ops Dashboard.
13. Confirm rollback readiness.
14. Mark release complete.

## Verification Commands
- `python scripts/run_release_signoff.py --mock`
- `python scripts/run_deployment_smoke.py --mock`
- `python scripts/run_full_live_qa_matrix.py --mock`
- `python scripts/build_ops_dashboard.py`
- `python scripts/run_research_eval.py --mock`
- `python -m py_compile scripts/run_release_signoff.py`

Optional live:
- `python scripts/run_release_signoff.py --live --base-url <production-or-staging-url>`
- `python scripts/run_deployment_smoke.py --live --base-url <production-or-staging-url>`
- `python scripts/run_full_live_qa_matrix.py --live --base-url <production-or-staging-url> --max-cases 5`

## Sign-off Record
- Release owner: `PENDING`
- Reviewer: `PENDING`
- Environment: `PENDING`
- Final deployment timestamp (UTC): `PENDING`
