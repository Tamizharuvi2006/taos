# TAOS RC Release Package (Phase 123)

## Candidate
- Proposed tag: `v0.123.0-rc1`
- Date: 2026-04-26

## Revision Snapshot
- Backend revision: unavailable in this workspace snapshot (`.git` metadata not present under `D:\agent\taos`).
- Frontend revision: unavailable in this workspace snapshot (`.git` metadata not queried in `D:\agent\frontend` during this pass).

## Verification Artifacts
- `QA_RESULTS_RELEASE_PREFLIGHT.json`
- `QA_RESULTS_RELEASE_PREFLIGHT.md`
- `QA_RESULTS_DEPLOYMENT_SMOKE.json`
- `QA_RESULTS_DEPLOYMENT_SMOKE.md`
- `QA_RESULTS_LIVE_FULL.json`
- `QA_RESULTS_LIVE_FULL.md`
- `QA_RESULTS_DOCUMENT.json`
- `QA_RESULTS_DOCUMENT.md`
- `PERFORMANCE_RESULTS.json`
- `PERFORMANCE_RESULTS.md`
- `PERFORMANCE_RESULTS_live_c1.json`
- `PERFORMANCE_RESULTS_live_c1.md`
- `PERFORMANCE_RESULTS_live_c2.json`
- `PERFORMANCE_RESULTS_live_c2.md`
- `PERFORMANCE_RESULTS_live_c5.json`
- `PERFORMANCE_RESULTS_live_c5.md`
- `QA_RESULTS_PERSISTENCE.json`
- `QA_RESULTS_PERSISTENCE.md`
- `docs/ops_dashboard_latest.json`
- `docs/ops_dashboard_latest.md`
- `eval/research_eval_report.md`

## Live Gate Outcome Summary
- Release preflight: passed (1 environment-specific blocked check: plain `python` missing on PATH).
- Deployment smoke live: passed.
- Full live QA matrix: passed.
- Document live QA: passed with optional fixture skips (`DOCUMENT_NOT_FOUND`).
- Persistence live QA: passed.
- Research eval mock: passed (overall score `0.922`).
- Frontend production build: passed.
- Performance live QA Phase 123B (strict gate, isolated reruns) passed:
  - c1: `error_rate=0.0`, `rate_limited_failures=0`, `unknown_route_failures=0`, `auth_failures=0`, `raw_internal_errors=0`, `p95=95.612ms`
  - c2: `error_rate=0.0`, `rate_limited_failures=0`, `unknown_route_failures=0`, `auth_failures=0`, `raw_internal_errors=0`, `p95=179.377ms`
  - c5: `error_rate=0.0`, `rate_limited_failures=0`, `unknown_route_failures=0`, `auth_failures=0`, `raw_internal_errors=0`, `p95=498.559ms`
- Root-cause handling preserved safety:
  - baseline failures were `429 RATE_LIMITED` under normal quota (not latency).
  - 429/4xx contract now includes safe route/error metadata (no more misleading unknown-route for known-route 429s).
  - dev perf quota path is explicit and development-only; production limits remain unchanged by default.

## Known Warnings
- Plain `python` executable not available on PATH in this environment.
- Optional live document fixtures missing in runtime; document QA cases safely skipped.

## Release Blockers
- No open technical release blockers after Phase 123B closure.
- Remaining requirement before final publication: release-owner/reviewer sign-off workflow.

## Recommendation
- RC package is technically ready for final sign-off.
- Publish RC tag after release-owner and reviewer approval.
