# Next Codex Start Here

Use this file first for the fastest local TAOS startup.

## 1. Start backend
```cmd
set PYTHONPATH=D:\agent&& set AUTH_ALLOW_DEV_BYPASS=true&& set ENTITY_LOOKUP_V1_ENABLED=true&& cd /d D:\agent\taos&& D:\agent\taos\.venv\Scripts\python.exe -m uvicorn taos.apps.api.main:app --host 127.0.0.1 --port 8000
```

## 2. Start frontend
```cmd
set NEXT_PUBLIC_API_URL=http://127.0.0.1:8000&& cd /d D:\agent\frontend&& npm.cmd run dev -- --hostname 127.0.0.1 --port 3000
```

## 3. Check backend
```powershell
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -ExpandProperty Content
```

## 4. Check frontend
```powershell
Invoke-WebRequest http://127.0.0.1:3000 | Select-Object -ExpandProperty StatusCode
```

## 5. Run live QA before browser
```powershell
cd D:\agent\taos
$env:PYTHONPATH='D:\agent'
$env:PYTHONIOENCODING='utf-8'
D:\agent\taos\.venv\Scripts\python.exe scripts\run_live_evidence_qa.py --live --base-url http://127.0.0.1:8000 --max-cases 10
```

## 6. Then open browser
- URL: `http://localhost:3000/chat`

## If entity/live truth is still wrong
Read next:
- `D:\agent\taos\docs\CODEX_LIVE_HANDOFF_2026-05-01.md`

## Current known blocker
- Direct engine path is better than the live HTTP entity response path.
- If Relyce CEO still comes back generic/unverified, the next fix is live API/entity handoff, not routing.
