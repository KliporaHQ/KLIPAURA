# Start Redis (Docker) + FastAPI Mission Control on :8000 for use with `next dev` on :3000.
# Next.js rewrites /api/* -> http://127.0.0.1:8000 (see next.config.js).
# Usage: npm run dev:api
#   or:  powershell -ExecutionPolicy Bypass -File ./scripts/run-api-local.ps1

# Continue: docker/pip may emit stderr; we still start uvicorn when possible.
$ErrorActionPreference = "Continue"
$McDir = Split-Path $PSScriptRoot -Parent
$Root = Split-Path $McDir -Parent

function Get-KlipProjectData {
  $b = [Environment]::GetEnvironmentVariable("KLIP_PROJECT_DATA", "Process")
  if ([string]::IsNullOrWhiteSpace($b)) { $b = [Environment]::GetEnvironmentVariable("KLIP_PROJECT_DATA", "User") }
  if ([string]::IsNullOrWhiteSpace($b)) { $b = "E:\ProjectData\KLIPAURA" }
  return $b
}

$redisLocalOk = $false
Push-Location $Root
Write-Host "[mc-api] Starting Redis (docker compose)..." -ForegroundColor Cyan
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& docker compose up -d redis 2>&1 | Out-Host
$dockerExit = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($dockerExit -eq 0) {
  $redisLocalOk = $true
} else {
  Write-Host "[mc-api] Docker Redis skipped (start Docker Desktop for local Redis). API will use REDIS_URL from repo .env via main.py." -ForegroundColor Yellow
}

if ($redisLocalOk) {
  Write-Host "[mc-api] Waiting for Redis on 127.0.0.1:6379..." -ForegroundColor DarkGray
  $deadline = (Get-Date).AddSeconds(45)
  $ready = $false
  while ((Get-Date) -lt $deadline) {
    try {
      $tcp = New-Object System.Net.Sockets.TcpClient
      $tcp.Connect("127.0.0.1", 6379)
      $tcp.Close()
      $ready = $true
      break
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  if (-not $ready) {
    Write-Host "[mc-api] Redis did not accept connections on 6379 in time. Check: docker compose ps redis" -ForegroundColor Yellow
  }
}
Pop-Location

Set-Location $McDir

$py = "python"
if (-not (Get-Command $py -ErrorAction SilentlyContinue)) {
  $py = "py"
}
Write-Host "[mc-api] Installing deps (klip-core + requirements)..." -ForegroundColor DarkGray
if (Test-Path "$Root\klip-core\setup.py") {
  & $py -m pip install -q -e "$Root\klip-core" 2>&1 | Out-Null
}
& $py -m pip install -q -r "$McDir\requirements.txt" 2>&1 | Out-Null

if ($redisLocalOk) {
  $env:REDIS_URL = "redis://127.0.0.1:6379/0"
  $env:USE_UPSTASH = "false"
}
$env:MC_AUTH_REQUIRED = "false"
if (-not $env:MC_ADMIN_USER) { $env:MC_ADMIN_USER = "klipaura2026" }
if (-not $env:MC_ADMIN_PASSWORD) { $env:MC_ADMIN_PASSWORD = "Klipaura123" }
$env:PYTHONPATH = "$Root;$Root\klip-core;$Root\klip-scanner;$Root\klip-mission-control"
$env:CORS_ALLOW_ORIGINS = "http://localhost:3000"
$env:CORS_ORIGINS = "http://localhost:3000"
$env:AVATAR_DATA_DIR = "$Root\klip-avatar\core_v1\data\avatars"
$projectData = Get-KlipProjectData
$jobsDir = Join-Path $projectData "jobs"
New-Item -ItemType Directory -Force -Path $jobsDir | Out-Null
$env:JOBS_DIR = $jobsDir
$pipCache = [Environment]::GetEnvironmentVariable("PIP_CACHE_DIR", "Process")
if ([string]::IsNullOrWhiteSpace($pipCache)) { $pipCache = [Environment]::GetEnvironmentVariable("PIP_CACHE_DIR", "User") }
if ([string]::IsNullOrWhiteSpace($pipCache)) { $pipCache = Join-Path $projectData "pip-cache" }
$env:PIP_CACHE_DIR = $pipCache
New-Item -ItemType Directory -Force -Path $env:PIP_CACHE_DIR | Out-Null
Write-Host "[mc-api] JOBS_DIR=$jobsDir" -ForegroundColor DarkGray

Write-Host '[mc-api] uvicorn main:app http://127.0.0.1:8000 (reload) - Next rewrites /api via next.config' -ForegroundColor Green
Write-Host '[mc-api] Open UI: http://localhost:3000' -ForegroundColor Green

& $py -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
