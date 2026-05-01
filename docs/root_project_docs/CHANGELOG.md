# Changelog

## 2026-04-24
- Added current-state project docs:
  `README.md`, `CURRENT_ARCHITECTURE.md`, `PRODUCTION_STATUS.md`, `ROADMAP.md`, `KNOWN_ISSUES.md`, `CHANGELOG.md`
- Added shared response-contract normalization helpers in `apps/api/response_contract.py`
- Extended `AgentResponse` with unified contract fields:
  `sections`, `route`, `warnings`, `metadata`
- Added reliability helpers in `core/reliability/*`
- Updated execute and execute/stream routes to use safer clarification and timeout fallbacks
- Added startup environment validation and richer health readiness checks
- Added deployment artifacts:
  `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- Expanded CI quality gate toward lint, typecheck, pytest, eval enforcement, and conditional frontend build
