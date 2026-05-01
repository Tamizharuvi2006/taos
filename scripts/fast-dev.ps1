param(
    [string]$Root = "D:\agent",
    [int]$FrontendPort = 3000,
    [int]$BackendPort = 8000
)

$ErrorActionPreference = "Stop"

$frontendDir = Join-Path $Root "frontend"
$backendDir = $Root

Write-Host "Starting backend (uvicorn)..." -ForegroundColor Cyan
Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$backendDir'; uvicorn taos.apps.api.main:app --host 0.0.0.0 --port $BackendPort"
)

Write-Host "Starting frontend (Next.js Turbopack)..." -ForegroundColor Cyan
Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$frontendDir'; npm run dev:turbo -- --port $FrontendPort"
)

Write-Host "Waiting for frontend to come up..." -ForegroundColor Yellow
$frontBase = "http://127.0.0.1:$FrontendPort"
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        Invoke-WebRequest -Uri "$frontBase/" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch {
        Start-Sleep -Milliseconds 800
    }
}

if (-not $ready) {
    Write-Host "Frontend not reachable yet. Backend + frontend windows are still running." -ForegroundColor Yellow
    exit 0
}

Write-Host "Prewarming / and /chat..." -ForegroundColor Green
try { Invoke-WebRequest -Uri "$frontBase/" -UseBasicParsing -TimeoutSec 10 | Out-Null } catch {}
try { Invoke-WebRequest -Uri "$frontBase/chat" -UseBasicParsing -TimeoutSec 10 | Out-Null } catch {}

Write-Host "Fast dev environment started." -ForegroundColor Green
Write-Host "Frontend: $frontBase" -ForegroundColor Green
Write-Host "Backend:  http://127.0.0.1:$BackendPort" -ForegroundColor Green
