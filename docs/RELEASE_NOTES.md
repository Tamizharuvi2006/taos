# TAOS Release Notes

## Release Candidate
- Tag: `v0.123.0-rc1`
- Date: `2026-04-26`
- Phase: `124` (Release Sign-off + Production Deployment Execution)

## Major Completed Phase Summary
- Phase 118: Security abuse hardening stabilized and passing.
- Phase 121: Performance load harness introduced and validated.
- Phase 122: Memory/chat persistence QA added and passing.
- Phase 123A: Reliability blocker root-cause isolated to rate limiting.
- Phase 123B: Rate-limit aware performance gate closure completed; strict reliability gate preserved.
- Phase 124: Release sign-off package and production deployment execution checklist finalized.

## Phase 123B Closure Highlights
- 429/4xx response contract now includes safe metadata (`error_code`, `code`, `request_id`, `route`, `owner`, `retry_after_seconds` when available).
- Performance runner now classifies `rate_limited_failures` separately from `unknown_route_failures`.
- Dev-only performance quota path is explicit and scoped; production bypass remains disabled by default.
- Live isolated `c1/c2/c5` reliability runs passed with zero rate-limited and unknown-route failures.

## Phase 124 Release Discipline Highlights
- Release sign-off script added: `scripts/run_release_signoff.py`.
- Release sign-off tests added: `tests/test_phase124_release_signoff.py`.
- Post-deploy verification runbook added: `docs/POST_DEPLOY_VERIFICATION.md`.
- Production sign-off checklist added: `docs/PRODUCTION_RELEASE_SIGNOFF.md`.

## Known Warnings
- Environment-specific: plain `python` executable may be missing on PATH in some Windows shells.
- Optional document QA fixtures may be absent in live runtime; those cases can be skipped with explicit reason.
- Full details: `docs/KNOWN_WARNINGS.md`.

## Rollback
- Rollback procedures and responsibilities are documented in `docs/ROLLBACK_PLAN.md`.

## Final Release State
- RC blocker status: closed.
- RC package: READY-for-signoff.
- Deployment posture: ready for release-owner/reviewer approval and controlled production execution.
