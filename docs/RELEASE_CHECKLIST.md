# TAOS Release Checklist

This checklist is the release gate for TAOS phase-based deployments.

## 1. Pre-release Environment Check
- [ ] Validate required environment variables.
- [ ] Run `python scripts/release_preflight.py --mock`.
- [ ] Confirm no production-unsafe settings (`DEBUG=true`, wildcard CORS, `AUTH_ALLOW_DEV_BYPASS=true` in prod).

## 2. Backend Health Check
- [ ] Backend starts cleanly.
- [ ] `/health` returns valid readiness payload.
- [ ] Run live smoke command:
  - `python scripts/run_deployment_smoke.py --live --base-url http://localhost:8000 --dev-user-id <id>`

## 3. Frontend Build Check
- [ ] Build frontend:
  - `cd D:\agent\frontend`
  - `npm run build`
- [ ] Record non-blocking local environment build warnings if present.

## 4. Mock QA Checks
- [ ] `python scripts/run_deployment_smoke.py --mock`
- [ ] `python scripts/run_full_live_qa_matrix.py --mock`
- [ ] `python scripts/run_document_live_qa.py --mock`
- [ ] `python scripts/run_research_eval.py --mock`

## 5. Live Smoke Checks
- [ ] `python scripts/run_deployment_smoke.py --live --base-url http://localhost:8000 --dev-user-id <id>`
- [ ] Confirm route checks and auth checks are passed or explicitly blocked with safe reason.

## 6. Live Full QA Checks
- [ ] `python scripts/run_full_live_qa_matrix.py --live --base-url http://localhost:8000 --max-cases 8`
- [ ] Confirm no contract/security regressions.

## 7. Live Document QA Checks
- [ ] `python scripts/run_document_live_qa.py --live --base-url http://localhost:8000 --max-cases 3`
- [ ] Fixture-unavailable cases are allowed as skips with explicit skip reason.

## 8. Ops Dashboard Generation
- [ ] `python scripts/build_ops_dashboard.py`
- [ ] Verify latest dashboard JSON and Markdown artifacts are updated.

## 9. Security Checks
- [ ] Verify sanitized trace behavior in smoke/live QA.
- [ ] Verify admin route protection remains intact.
- [ ] Verify no secret leakage in generated QA artifacts.

## 10. Rollback Steps
- [ ] Read and confirm rollback plan in `docs/ROLLBACK_PLAN.md`.
- [ ] Rollback checklist includes both backend and frontend rollback paths.
- [ ] Validate post-rollback smoke and QA.

## 11. Release Sign-off
- [ ] Release owner and reviewer sign-off recorded.
- [ ] Environment + version + deployment timestamp recorded.
- [ ] Verification artifact links recorded (`QA_RESULTS_*.json|.md`, ops dashboard).
