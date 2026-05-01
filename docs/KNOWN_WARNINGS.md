# TAOS Known Warnings / Non-Blockers

## Python executable differences
- Some Windows setups do not resolve plain `python` on PATH.
- Use Python 3.13 explicitly when needed (`py -3.13 ...`) if `python` is unavailable.
- This is an environment warning, not a TAOS logic failure.

## Firebase / Firestore runtime warnings
- If Firebase is configured but Firestore runtime is unavailable, readiness may report warning/degraded state.
- TAOS fallback behavior should remain safe in memory-backed mode for local/mock QA.

## ALTS / gRPC environment warnings (when Firebase libraries are present)
- ALTS/gRPC platform warnings can appear in local development environments.
- Treat these as non-blockers unless they correlate with real request failures.

## Live document fixture availability
- `run_document_live_qa.py --live` may skip fixture-dependent cases when document IDs are not uploaded in the live runtime.
- Fixture-unavailable skip is expected and non-blocking.

## Frontend local build environment warnings
- Local frontend builds may hit environment-specific issues such as `spawn EPERM` or worker-memory constraints.
- Record these as environment warnings when backend release checks are still healthy.
