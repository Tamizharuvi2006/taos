# TAOS Rollback Plan

## Rollback Triggers
- Critical deployment smoke failure.
- Critical live QA failure on auth/security/contract.
- Sustained error-rate or timeout regression after release.

## Backend Rollback
1. Identify last known good backend commit/artifact.
2. Redeploy backend to previous known good release.
3. Restart backend service and validate:
   - `python scripts/run_deployment_smoke.py --live --base-url http://localhost:8000 --dev-user-id <id>`
4. Run QA sanity:
   - `python scripts/run_full_live_qa_matrix.py --live --base-url http://localhost:8000 --max-cases 8`
5. Record rollback timestamp, owner, and reason.

## Frontend Rollback
1. Identify last known good frontend build in `D:\agent\frontend`.
2. Re-deploy previous frontend artifact/version.
3. Rebuild locally if required:
   - `cd D:\agent\frontend`
   - `npm run build`
4. Validate frontend-to-backend integration with smoke checks.
5. Record rollback timestamp, owner, and reason.

## Data/Persistence Considerations
- Avoid destructive data operations during rollback.
- If persistence backend changes are involved, verify user/chat/task read/write consistency after rollback.

## Post-rollback Validation
- `python scripts/run_deployment_smoke.py --mock`
- `python scripts/run_full_live_qa_matrix.py --mock`
- `python scripts/run_document_live_qa.py --mock`
- `python scripts/build_ops_dashboard.py`

## Communication Checklist
- Declare rollback status in ops channel.
- Share impact window and mitigation state.
- Confirm restored service status with verification artifact links.
