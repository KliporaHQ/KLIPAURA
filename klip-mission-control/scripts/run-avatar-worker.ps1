# Run klip-avatar queue worker locally (consumes klipaura:jobs:pending).
# Requires: Python 3.11+, ffmpeg on PATH, repo .env with REDIS_URL and API keys.
# From repo root: pip install -e klip-core && pip install -r klip-avatar/requirements.txt
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$AvatarDir = Join-Path $Root "klip-avatar"
$KlipCore = Join-Path $Root "klip-core"

if (-not (Test-Path $AvatarDir)) {
    Write-Error "klip-avatar not found at $AvatarDir"
    exit 1
}

# Match Mission Control local API for job status callbacks (worker uses get_settings().master_mc_url).
if (-not $env:MASTER_MC_URL) { $env:MASTER_MC_URL = "http://127.0.0.1:8000" }
if (-not $env:MC_URL) { $env:MC_URL = $env:MASTER_MC_URL }

# Jobs manifest + R2 sync paths (same defaults as docker-compose klip-avatar service)
if (-not $env:JOBS_DIR) {
    $dataRoot = if ($env:KLIP_PROJECT_DATA) { $env:KLIP_PROJECT_DATA } else { Join-Path $Root "data" }
    $env:JOBS_DIR = Join-Path $dataRoot "jobs"
}

$env:PYTHONPATH = "$KlipCore;$AvatarDir"
if ($env:PYTHONPATH_EXTRA) {
    $env:PYTHONPATH = "$env:PYTHONPATH_EXTRA;$env:PYTHONPATH"
}

Set-Location $AvatarDir
Write-Host "[avatar-worker] cwd=$AvatarDir" -ForegroundColor Green
Write-Host "[avatar-worker] MASTER_MC_URL=$env:MASTER_MC_URL JOBS_DIR=$env:JOBS_DIR" -ForegroundColor Gray
python -m klip_avatar.worker
