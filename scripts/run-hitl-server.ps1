# Run FastAPI Mission Control (klip-dispatch/hitl_server.py) — OS-aligned API.
# Usage from repo root: powershell -ExecutionPolicy Bypass -File .\scripts\run-hitl-server.ps1
# Requires: pip install -r requirements.txt, Redis in .env

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

Set-Location $Root
$env:PYTHONPATH = "$Root;$Root\klip-scanner"
if (-not $env:PORT) { $env:PORT = "8080" }

Write-Host "[hitl] PYTHONPATH includes klip-scanner; uvicorn hitl_server on port $($env:PORT)" -ForegroundColor Green
python -m uvicorn hitl_server:app --app-dir klip-dispatch --host 0.0.0.0 --port $env:PORT --reload
