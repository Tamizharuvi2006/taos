# TAOS Incident Response

## 1. Detect and Classify
- Capture alert/source and first timestamp.
- Classify severity:
  - Sev1: production down, severe data/auth risk.
  - Sev2: degraded core function.
  - Sev3: non-critical degradation.

## 2. Stabilize
- Freeze new rollout actions.
- Confirm current health and smoke status.
- If needed, execute rollback from `docs/ROLLBACK_PLAN.md`.

## 3. Triage
- Identify whether issue is:
  - backend service/runtime
  - frontend build/deploy
  - external provider dependency
  - persistence/storage/Firebase
  - regression from recent release

## 4. Execute Runbook Checks
- `python scripts/run_deployment_smoke.py --live --base-url http://localhost:8000 --dev-user-id <id>`
- `python scripts/run_full_live_qa_matrix.py --live --base-url http://localhost:8000 --max-cases 8`
- `python scripts/run_document_live_qa.py --live --base-url http://localhost:8000 --max-cases 3`

## 5. Containment and Recovery
- Apply minimal safe fix or rollback.
- Re-run smoke and targeted QA until stable.
- Regenerate ops dashboard:
  - `python scripts/build_ops_dashboard.py`

## 6. Communication
- Report:
  - start time
  - impact
  - mitigation action
  - current status
  - ETA or next checkpoint

## 7. Postmortem Checklist
- Root cause summary.
- What detection missed.
- Preventive actions (tests/runbook/checklist updates).
- Attach relevant QA/ops artifacts.
