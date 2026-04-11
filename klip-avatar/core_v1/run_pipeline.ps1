# Post-run: tee logs + append AUTO RUN UPDATE to KLIPAURA_REBOOT_MASTER_CONTEXT.md
#
# --- SHIP GATE (binary exit — do not re-tune filters after this) ---
# PASS = detect_product_usage: max(m12, m23) >= 0.042 (see DEBUG line before [7/7]).
# Run 1: $env:UGC_DEBUG_PRODUCT_MAD = "1"  then .\run_pipeline.ps1
# If FAIL only: Run 2: $env:AFFILIATE_TOP_BAND_MAD_BOOST_PX = "16"  then rerun (ceiling 18).
# If PASS: ship FINAL_VIDEO.mp4 — no more pipeline math changes for this gate.
# Optional: add UGC_LOG_SHIP_GATE=1 to .env to print effective split + MAD_BOOST_PX (catch .env overrides).
# -------------------------------------------------------------------
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

# Unlimited I2V/hour for this process (ugc_pipeline re-applies after dotenv when KLIP_PIPELINE_RUN=1).
$env:KLIP_PIPELINE_RUN = "1"
$env:WAVESPEED_MAX_I2V_PER_HOUR = "0"

if (-not $env:UGC_DEBUG_PRODUCT_MAD) {
    $env:UGC_DEBUG_PRODUCT_MAD = "1"
}

if (-not (Test-Path -LiteralPath "outputs")) {
    New-Item -ItemType Directory -Path "outputs" -Force | Out-Null
}

python pipeline/ugc_pipeline.py 2>&1 | Tee-Object -FilePath "outputs\last_run.log"

python scripts\update_context.py
