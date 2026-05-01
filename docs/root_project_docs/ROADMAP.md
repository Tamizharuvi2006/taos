# Roadmap

## Phase 95
Reliability Lock

Goals:
- never return `None`
- reduce timeout-induced weak results
- make fallback behavior predictable
- handle messy casual and ambiguous prompts safely

Tracks:
- request and stage budgeting
- timeout fallback builder
- ambiguity clarification fallback
- route stability for multilingual and casual prompts
- targeted timeout regression coverage

## Phase 96
Evidence And Citation Engine

Goals:
- make claim support visible and auditable
- strengthen trust block meaning
- improve confidence calibration

Targets:
- evidence matrix
- claim extraction
- citation support checking
- source conflict detection
- automatic confidence penalties for weak or conflicting evidence

## Phase 97
Unified Response Contract

Goals:
- one stable frontend/backend contract
- fewer route-specific payload surprises

Envelope direction:
- `answer`
- `sections`
- `route`
- `mode`
- `confidence`
- `sources`
- `trust_block`
- `trace`
- `warnings`
- `metadata`

## Phase 98
Production Ops

Goals:
- safer deployability
- easier environment validation
- cleaner CI/CD

Targets:
- Docker and compose
- readiness/health hardening
- CI for lint, type check, tests, evals, frontend build when present
- rate limiting and cost visibility
- deployment notes and env templates

## Phase 99
Evaluation Dashboard

Goals:
- make progress visible over time
- quantify reliability rather than describing it loosely

Metrics:
- overall eval score
- citation quality
- timeout rate
- confidence calibration
- Tamil/Tanglish routing quality
- document-grounding quality
