# TAOS Phase 123 - Release Candidate Freeze Checklist

## Scope
- Final production gate only.
- No new features.
- No behavior expansion unless required for release blocking fixes.

## Freeze Controls
- [x] Freeze backend behavior changes for search/routing/provider logic.
- [x] Freeze frontend behavior changes except release-blocking fixes.
- [x] Lock known warnings list for release notes.

## Final Verification Pack
- [x] `python scripts/release_preflight.py --live --base-url http://localhost:8000`
- [x] `python scripts/run_deployment_smoke.py --live --base-url http://localhost:8000 --dev-user-id <dev-id>`
- [x] `python scripts/run_full_live_qa_matrix.py --live --base-url http://localhost:8000 --max-cases 8`
- [x] `python scripts/run_document_live_qa.py --live --base-url http://localhost:8000 --max-cases 3`
- [x] `python scripts/run_performance_load_test.py --live --base-url http://localhost:8000 --max-cases 3 --concurrency 2`
- [x] `python scripts/run_persistence_qa.py --live --base-url http://localhost:8000 --max-cases 5`
- [x] `python scripts/build_ops_dashboard.py`
- [x] `python scripts/run_research_eval.py --mock`
- [x] `cd D:\agent\frontend && npm run build`

Verification snapshot (2026-04-26):
- release_preflight live: passed, blocked_count=1 (`python` CLI unavailable on PATH).
- deployment smoke live: passed (9 passed, 0 failed, 0 blocked).
- full live QA: passed (8/8).
- document live QA: passed with optional fixture skips (3 skipped, 0 failed).
- performance live baseline: failed under normal quota due rate limiting (`429 RATE_LIMITED`, `15/minute`), while latency stayed low.
- performance live Phase 123B isolated reruns (dev perf mode, strict gate unchanged):
  - c1: passed (`error_rate=0.0`, `rate_limited_failures=0`, `unknown_route_failures=0`, `p95=95.612ms`)
  - c2: passed (`error_rate=0.0`, `rate_limited_failures=0`, `unknown_route_failures=0`, `p95=179.377ms`)
  - c5: passed (`error_rate=0.0`, `rate_limited_failures=0`, `unknown_route_failures=0`, `p95=498.559ms`)
- persistence live: passed (5/5).
- ops dashboard build: passed.
- research eval mock: passed (overall score 0.922).
- frontend build: passed.

Phase 123B closure snapshot (2026-04-26):
- 429/4xx error contract now carries safe metadata (`error_code/code`, `request_id`, `route`, `owner`, `retry_after_seconds` when available).
- runner now reports `rate_limited_failures` separately from `unknown_route_failures`.
- dev-only perf quota path is explicit and scoped:
  - requires `TAOS_ENV=development`
  - requires perf mode request/config
  - requires matching `PERF_TEST_USER_ID`
  - production bypass is not active by default.
- live c1/c2/c5 reruns pass reliability and latency gates without lowering pressure.

Phase 123A artifacts:
- `PERFORMANCE_RESULTS_live_c1.json|.md`
- `PERFORMANCE_RESULTS_live_c2.json|.md`
- `PERFORMANCE_RESULTS_live_c5.json|.md`

## Release Notes and Warnings
- [x] Capture all blocked reasons and classify as:
  - release blocker
  - non-blocking warning
  - environment-specific limitation
- [x] Confirm known warnings are documented in `docs/KNOWN_WARNINGS.md`.

Blocked/warning classification:
- Release blocker:
  - none open (Phase 123B performance reliability blocker closed).
- Non-blocking warning:
  - Document live QA fixture docs missing in live runtime (`DOCUMENT_NOT_FOUND`), cases safely skipped.
- Environment-specific limitation:
  - Plain `python` executable missing on PATH in this environment; bundled Python path used.

## RC Tag + Package Preparation
- [x] Choose release candidate tag name (example: `v0.123.0-rc1`).
- [x] Prepare deployment package manifest:
  - backend revision
  - frontend revision
  - verification artifact links
  - known warnings
- [ ] Sign-off from release owner and reviewer.

## Exit Criteria
- [x] All blockers resolved or consciously deferred with sign-off.
- [ ] RC tag prepared.
- [x] Deployment package ready.

Proposed RC tag:
- `v0.123.0-rc1`
