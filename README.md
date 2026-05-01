# TAOS

Task-Aware Orchestration System: an agent runtime for reliable execution, research, document QA, and task automation.

TAOS is no longer a prototype with one planner loop. The current system includes:
- intent and route selection for `fast_message`, `standard_task`, `deep_research`, and `doc_mode`
- an FSM-driven orchestration engine with planning, execution, reflection, and replanning
- research and evidence pipelines with trust and trace output
- document upload, retrieval, and ask flows
- persistent chats, task scheduling, notifications, and Firebase-backed storage options

The project is strong architecturally, but the current focus is reliability, evidence quality, response-contract consistency, and deployment hardening rather than adding more "brain" features.

## Current Docs
- [CURRENT_ARCHITECTURE.md](/D:/agent/taos/CURRENT_ARCHITECTURE.md)
- [PRODUCTION_STATUS.md](/D:/agent/taos/PRODUCTION_STATUS.md)
- [ROADMAP.md](/D:/agent/taos/ROADMAP.md)
- [KNOWN_ISSUES.md](/D:/agent/taos/KNOWN_ISSUES.md)
- [CHANGELOG.md](/D:/agent/taos/CHANGELOG.md)
- [ARCHITECTURE.md](/D:/agent/taos/ARCHITECTURE.md)
- [DEVELOPMENT_LOG.md](/D:/agent/taos/DEVELOPMENT_LOG.md)

## Current API
Primary execution:
- `POST /execute`
- `POST /execute/stream`

Core supporting routes:
- `GET /health`
- `GET /warmup`
- document routes under `/api/*`
- task routes under `/tasks/*`
- workflow routes under `/workflows/*`
- notification, chat, push, billing, and user routes

## Unified Response Contract
Execution responses are being standardized around one envelope:

```json
{
  "answer": "string",
  "direct_answer": "string",
  "sections": [],
  "route": "standard_task",
  "mode": "standard",
  "confidence": 0.0,
  "sources": [],
  "trust_block": {},
  "trace": {},
  "warnings": [],
  "metadata": {}
}
```

Legacy fields such as `answer_sections`, `frontend_hints`, and route-specific metadata still exist for compatibility, but the direction is one stable client contract.

## Reliability Direction
The active hardening track is:
- request and stage budgeting
- safe timeout fallbacks
- ambiguity clarification fallbacks
- multilingual and casual-message fast routing
- better evidence support and confidence calibration
- deployment and startup validation

## Run Locally
Python API:

```bash
python -m uvicorn taos.apps.api.main:app --host 0.0.0.0 --port 8000
```

Docker:

```bash
docker compose up --build
```

## Health And Ops
- `/health` now exposes readiness and startup validation checks
- startup validation warns about missing runtime configuration
- CI quality gate covers lint, type checking, pytest regressions, evaluation thresholds, and a conditional frontend build

## Status
TAOS is close to production quality, but this repo should currently be described as:

`architecture-strong, reliability-hardening in progress, production hardening underway`
