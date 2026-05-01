# TAOS Release Preflight

- Generated: 2026-04-30T17:46:59.594771+00:00
- Mode: `mock`
- Passed: **16**
- Failed: **0**
- Blocked: **1**

## Required Environment Variables
- `TAOS_ENV`
- `OPENROUTER_API_KEY`
- `SERPER_API_KEY`
- `STORAGE_BACKEND`
- `FIREBASE_PROJECT_ID`
- `FIREBASE_CLIENT_EMAIL`
- `FIREBASE_PRIVATE_KEY`

| Check | Status | Passed | Blocked | Detail |
| --- | --- | ---: | ---: | --- |
| `environment_ready` | passed | yes | no | readiness_status=ready |
| `health_contract_available` | passed | yes | no | - |
| `provider_health_rows_available` | passed | yes | no | - |
| `runbook_doc_exists` | passed | yes | no | D:\agent\taos\docs\RUNBOOK.md |
| `release_checklist_doc_exists` | passed | yes | no | D:\agent\taos\docs\RELEASE_CHECKLIST.md |
| `rollback_doc_exists` | passed | yes | no | D:\agent\taos\docs\ROLLBACK_PLAN.md |
| `incident_doc_exists` | passed | yes | no | D:\agent\taos\docs\INCIDENT_RESPONSE.md |
| `known_warnings_doc_exists` | passed | yes | no | D:\agent\taos\docs\KNOWN_WARNINGS.md |
| `smoke_script_exists` | passed | yes | no | D:\agent\taos\scripts\run_deployment_smoke.py |
| `full_qa_script_exists` | passed | yes | no | D:\agent\taos\scripts\run_full_live_qa_matrix.py |
| `document_qa_script_exists` | passed | yes | no | D:\agent\taos\scripts\run_document_live_qa.py |
| `ops_dashboard_script_exists` | passed | yes | no | D:\agent\taos\scripts\build_ops_dashboard.py |
| `research_eval_script_exists` | passed | yes | no | D:\agent\taos\scripts\run_research_eval.py |
| `frontend_root_exists` | passed | yes | no | D:\agent\frontend |
| `frontend_build_artifact_exists` | passed | yes | no | Expect .next/BUILD_ID or .next/server in D:\agent\frontend |
| `python_cli_available` | blocked | no | yes | Use py -3.13 if plain python is unavailable. |
| `python_runtime_supported` | passed | yes | no | runtime=3.12.13 |
