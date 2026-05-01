$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
Set-Location $workspaceRoot

$uvicornArgs = @(
  "-m",
  "uvicorn",
  "taos.apps.api.main:app",
  "--host",
  "127.0.0.1",
  "--port",
  "8000"
)

$proc = $null
$stdoutLog = Join-Path $projectRoot "tmp_phase94_uvicorn_out.log"
$stderrLog = Join-Path $projectRoot "tmp_phase94_uvicorn_err.log"
try {
  $env:AUTH_ALLOW_DEV_BYPASS = "true"
  if (Test-Path $stdoutLog) { Remove-Item -LiteralPath $stdoutLog -Force }
  if (Test-Path $stderrLog) { Remove-Item -LiteralPath $stderrLog -Force }
  $proc = Start-Process `
    -FilePath "python" `
    -ArgumentList $uvicornArgs `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog

  $ready = $false
  for ($i = 0; $i -lt 60; $i++) {
    try {
      $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2
      if ($resp.StatusCode -eq 200) {
        $ready = $true
        break
      }
    }
    catch {
      Start-Sleep -Milliseconds 500
    }
  }

  if (-not $ready) {
    if ($proc -and $proc.HasExited) {
      Write-Host "Uvicorn process exited early with code $($proc.ExitCode)."
    }
    if (Test-Path $stdoutLog) {
      Write-Host "---- uvicorn stdout (tail) ----"
      Get-Content -LiteralPath $stdoutLog -Tail 80
    }
    if (Test-Path $stderrLog) {
      Write-Host "---- uvicorn stderr (tail) ----"
      Get-Content -LiteralPath $stderrLog -Tail 80
    }
    throw "Server did not become ready on http://127.0.0.1:8000/health"
  }

  python -m taos.scripts.run_intelligence_eval `
    --base-url http://127.0.0.1:8000 `
    --use-bypass-header `
    --cases taos/tests/fixtures/intelligence_eval_cases_v2.json `
    --timeout 20 `
    --out taos/docs/intelligence_eval_phase94_v2_full.json

  python -m taos.scripts.run_intelligence_eval `
    --base-url http://127.0.0.1:8000 `
    --use-bypass-header `
    --cases taos/tests/fixtures/intelligence_eval_cases.json `
    --timeout 20 `
    --out taos/docs/intelligence_eval_phase94_base_full.json

  python -m taos.scripts.run_research_eval `
    --base-url http://127.0.0.1:8000 `
    --cases taos/tests/fixtures/research_eval_cases.json `
    --use-bypass-header `
    --out taos/docs/research_eval_phase94_full.json
}
finally {
  if ($proc -and -not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force
  }
}
